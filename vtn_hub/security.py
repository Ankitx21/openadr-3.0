"""Bounded, shared-cache request admission before database work."""
import hashlib
import math
import time

from django.core.cache import cache
from django.http import JsonResponse
from django.core.exceptions import RequestDataTooBig


class ServiceBusy(Exception):
    pass


def error(message, status=400, retry=None):
    response = JsonResponse({"status": "error", "message": message, **(
        {"retry_after_seconds": retry} if retry else {})}, status=status)
    if retry:
        response["Retry-After"] = str(retry)
    response["Cache-Control"] = "no-store, private"
    return response


def admit(identity, scope, limit, seconds, cost=1):
    """Atomic fixed window counters. Keys expire; Redis shares them across workers."""
    now = time.time()
    key = "guard:" + hashlib.sha256(f"{scope}:{identity}:{int(now // seconds)}".encode()).hexdigest()
    if cache.add(key, cost, timeout=seconds + 1):
        count = cost
    else:
        try:
            count = cache.incr(key, cost)
        except ValueError:
            return False, 1
    return count <= limit, max(1, math.ceil(seconds - now % seconds))


def throttle(identity, scope, rules, cost=1):
    for limit, seconds in rules:
        try:
            allowed, retry = admit(identity, f"{scope}:{seconds}", limit, seconds, cost)
        except Exception as exc:
            raise ServiceBusy("Request protection is temporarily unavailable.") from exc
        if not allowed:
            return error("Too many requests. Wait before retrying; cache tariffs and send telemetry every 15 minutes.", 429, retry)
    return None


class RequestGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        guarded = request.path.startswith(("/api/", "/download/", "/login/", "/admin/")) or request.path in ("/", "/analytics/")
        if guarded:
            try:
                length = int(request.META.get("CONTENT_LENGTH") or 0)
                if length < 0 or length > 65536:
                    return error("Request body must be at most 64 KB.", 413)
                if request.method in ("POST", "PUT", "PATCH") and len(request.body) > 65536:
                    return error("Request body must be at most 64 KB.", 413)
            except RequestDataTooBig:
                return error("Request body must be at most 64 KB.", 413)
            except (ValueError, OverflowError):
                return error("Invalid Content-Length.")
            # Use the socket peer, never a caller-controlled X-Forwarded-For value.
            identity = request.META.get("REMOTE_ADDR", "unknown")
            try:
                blocked = throttle(identity, "ip", [(240, 60)])
                if blocked is None and request.path.startswith(("/login/", "/admin/login")) and request.method == "POST":
                    blocked = throttle(identity, "login", [(10, 300)])
                if blocked is not None:
                    return blocked
            except Exception:
                return error("Request protection is temporarily unavailable. Try again shortly.", 503, 30)
        response = self.get_response(request)
        if guarded or request.path in ("/", "/analytics/"):
            response["Cache-Control"] = "no-store, private"
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, ServiceBusy):
            return error(str(exception), 503, 5)
