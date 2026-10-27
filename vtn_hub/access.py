"""Dashboard roles are evaluated server-side on every request, never from URL/session claims."""
from django.contrib.auth.models import Permission
from django.db import transaction
from .models import DashboardAccount


def access_for(user):
    if not user.is_authenticated or not user.is_active:
        return None, None, None
    account = DashboardAccount.objects.select_related('team').filter(user=user).first()
    if user.is_superuser:
        return 'organizer', None, account
    if account:
        if account.role in ('team', 'jury') and user.is_staff:
            return None, None, account
        return account.role, account.team, account
    if user.is_staff:
        return 'organizer', None, None  # Existing organizer accounts continue to work.
    return None, None, None


def request_access(request):
    if not hasattr(request, '_dashboard_access'):
        request._dashboard_access = access_for(request.user)
    return request._dashboard_access


def dashboard_context(request):
    role, team, account = request_access(request)
    return {'dashboard_role': role, 'dashboard_team': team,
            'is_organizer': role == 'organizer', 'is_jury': role == 'jury',
            'can_view_all': role in ('organizer', 'jury'), 'can_export': bool(role),
            'role_label': {'organizer': 'Organizer', 'jury': 'Jury', 'team': 'Team'}.get(role, ''),
            'must_change_password': bool(account and account.must_change_password)}


@transaction.atomic
def save_dashboard_account(user, role, team=None):
    if role not in dict(DashboardAccount.ROLE_CHOICES) or (role == 'team') != bool(team):
        raise ValueError('Select a team only for a team account.')
    user.is_staff = role == 'organizer'
    user.is_superuser = False
    user.full_clean(exclude=['password'])
    user.save()
    account = DashboardAccount(user=user, role=role, team=team, must_change_password=True)
    account.full_clean()
    account.save()
    if role == 'organizer':
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='vtn_hub'))
    return account
