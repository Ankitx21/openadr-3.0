# Student connection guide

Use the Python client or HTTP API with the token assigned privately by your organizer. For browser access, ask the organizer for your team dashboard username and temporary password. You will set a separate dashboard password at first login and can then view only your own team analytics and exports. You do not need dashboard or organizer credentials for device API calls; use the existing API token without a dashboard-session cookie.

## Python client

Install `requests` if available (`python -m pip install -r requirements.txt`). The client also supports Python's built-in HTTP library. Run `python openadr3_client.py --server https://YOUR-SERVER` for the interactive menu, or import it:

```python
from openadr3_client import TeamVen

ven = TeamVen(token='YOUR_ASSIGNED_TOKEN', server_url='https://YOUR-SERVER')
ven.verify_token()
prices = ven.get_tariffs()  # cached within this client instance for one hour
ven.send_testing(energy=0.25, appliance='AC', outdoor_temperature=31)
# After a complete real measurement interval, submit the measured energy:
# ven.send_baseline(energy=measured_kwh, appliance='AC', outdoor_temperature=31)
# ven.send_prototype(energy=measured_kwh, appliance='AC', outdoor_temperature=31)
```

Keep tokens private. The client sends authorization headers, not token query strings. Do not commit tokens or print them into logs. Never put your organizer password into a device.

## API contract

Use `Authorization: Bearer YOUR_ASSIGNED_TOKEN` and `Content-Type: application/json`.

| Route | Purpose |
| --- | --- |
| GET `/api/v3/auth/verify` | Check token and identify your team |
| GET `/api/v3/team/status` | Your team's saved reading counts |
| GET `/api/v3/tariffs` | 48 hourly prices, today + tomorrow, INR/kWh |
| POST `/api/v3/testing` | Connection tests, retained separately from competition data |
| POST `/api/v3/baseline` | Measured baseline interval energy |
| POST `/api/v3/prototype` | Measured optimized interval energy |
| GET `/api/v3/baseline?limit=100` | Recent readings for your team |
| GET `/api/v3/telemetry/intervals?phase=baseline&range=24h` | Your team's interval chart data |

Do not include another team's name in a request. Organizer publishing and cross-team standings are restricted. Your authenticated team dashboard can export only your own team data.

Example body:

```json
{
  "appliances": {
    "Smart_AC": {"energy": 0.25},
    "Water_Heater": {"energy": 0.50}
  },
  "outdoor_temperature": 31.5
}
```

Send energy consumed during the interval, in kWh. A constant 1 kW load running for 15 minutes consumes 0.25 kWh. Instantaneous `power_kw` is not interval energy and is rejected unless a valid `energy` or `energy_kwh` is also supplied. For varying power, integrate sensor readings over the interval. Hardware examples use a constant-power assumption only to demonstrate unit conversion.

Send one batch every 15 minutes, up to 12 uniquely named appliances per phase. Reusing the same name in the same 15-minute slot updates the previous reading. A successful request returns HTTP 201. The optional timestamp must be ISO 8601 with a timezone (for example `2026-10-26T10:15:00+05:30`), within the past 31 days or at most 5 minutes ahead. Without a timestamp the server uses receipt time. Timestamps round forward to an interval ending boundary; align submissions to interval boundaries and include measurement timestamps where possible. Notes may contain up to 1,000 characters. Testing retains only the latest 20 readings per team.

## Avoid request loops

Fetch tariffs once and cache them; refresh once an hour. Keep at least 2 seconds between telemetry requests, including testing requests. Budgets are shared across all your team's devices and all three phases: 24 appliance readings per minute and 120 per hour. This supports 12 appliances batched every 15 minutes. Tariff budgets are 6 per minute and 30 per hour, with a 5-second spacing limit.

On HTTP 429, read `Retry-After`, wait that many seconds, and add a little random delay. Do not retry in a tight loop. On 400 fix the payload; on 401 check your token with the organizer; on 403 check which endpoint you used; on 503 wait before trying again. The Python client surfaces errors and never automatically retries telemetry POSTs.

`simulate_appliance.py --token YOUR_ASSIGNED_TOKEN --server https://YOUR-SERVER` compares equal-energy price schedules locally. It never submits forecasts as real baseline/prototype data. Add `--send-test` only to send a clearly marked synthetic connection test.
