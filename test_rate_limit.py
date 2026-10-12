import json
import urllib.error
import urllib.request

URL = "https://openadr-3-0.onrender.com/api/v3/reports?token=KEY-TEAM07-G2B9"

# -------------------------------------------------------------
# TEST 1: Per-Appliance 2-Second Cooldown
# -------------------------------------------------------------
print("=" * 60)
print("TEST 1: Testing 2-Second Cooldown (Same Appliance)")
print("=" * 60)

single_payload = json.dumps({"appliances": {"Test_Appliance": 2.5}}).encode("utf-8")

for i in range(1, 3):
    req = urllib.request.Request(
        URL,
        data=single_payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req)
        print(f"Request {i}: SUCCESS (HTTP {resp.status}) - Saved to DB")
    except urllib.error.HTTPError as err:
        print(f"Request {i}: BLOCKED (HTTP {err.code}) -> {err.read().decode().strip()}")


# -------------------------------------------------------------
# TEST 2: Max 60 Reports Per Minute Limit
# -------------------------------------------------------------
print("\n" + "=" * 60)
print("TEST 2: Testing Max 60 Reports Per Minute Limit")
print("=" * 60)

# We send 65 appliances in one batch (exceeds the 60/min limit)
flood_payload = json.dumps({
    "appliances": {f"Sensor_{i}": 1.0 for i in range(65)}
}).encode("utf-8")

req = urllib.request.Request(
    URL,
    data=flood_payload,
    headers={"Content-Type": "application/json"}
)

try:
    resp = urllib.request.urlopen(req)
    print(f"Result: SUCCESS (HTTP {resp.status})")
except urllib.error.HTTPError as err:
    print(f"Result: BLOCKED (HTTP {err.code}) -> {err.read().decode().strip()}")

print("\n" + "=" * 60)
