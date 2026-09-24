# Grid-Interactive Competition: Student Participant Guide (OpenADR 3.0)

Welcome to the Grid-Interactive Appliance Optimization Challenge.

In this competition, your team acts as a Virtual End Node (VEN) communicating with the organizer Virtual Top Node (VTN) server using the OpenADR 3.0 standard.

---

## 1. Authentication and Access Control

To access competition data and submit telemetry, your team must use its assigned unique **API Token** (e.g. `KEY-TEAM01-A7F9`).

If an invalid or missing token is provided, the client outputs:
```text
Invalid token.
```
and exits.

---

## 2. Downloading 2-Day Ahead Tariffs (Direct JSON & CSV)

The server provides a 48-hour Day-Ahead horizon (Today + Tomorrow) in INR/kWh:

### Option A: Using the Python Client
```bash
python openadr3_client.py --token "YOUR_ASSIGNED_TOKEN" --server "https://your-server.onrender.com" --fetch-tariffs
```
This saves:
- `tariffs_received.json`: 48-hour Day-Ahead tariffs in JSON format.
- `tariffs_received.csv`: Formatted spreadsheet for Excel and Pandas.

### Option B: Interactive Menu Mode
Run without arguments:
```bash
python openadr3_client.py
```
Enter your assigned token and select option `1` to download tariffs.

### Option C: Direct HTTP Request (ESP32 / Microcontrollers)
```bash
curl -X GET "https://your-server.onrender.com/api/v3/tariffs.json?token=YOUR_ASSIGNED_TOKEN"
```

---

## 3. Reporting Telemetry for Multiple Appliances & Microcontrollers

A team can have multiple appliances, each controlled by its own microcontroller (e.g., separate ESP32s on AC, Water Heater, and EV Charger) using the same team token.

### Rate Limiting Rules
To prevent accidental infinite loops from microcontrollers:
1. **Per-Appliance Minimum Interval**: Minimum **2 seconds** required between reports for the same appliance.
2. **Team Burst Limit**: Maximum **60 reports per minute** across all microcontrollers for a team.
3. Exceeding these limits returns `HTTP 429 Too Many Requests`.

### Python Integration
```python
from openadr3_client import TeamVen

ven = TeamVen(token="KEY-TEAM01-A7F9", server_url="https://your-server.onrender.com")

# 1. Fetch 2-Day Ahead Tariffs
tariffs = ven.get_tariffs()

# 2. Report from an individual microcontroller/appliance
ven.report_power(power_kw=1.85, appliance="Smart_AC", status="RUNNING")

# 3. Or report multiple appliances simultaneously
ven.report_multi_appliances({
    "Smart_AC": 1.5,
    "Water_Heater": 2.1,
    "EV_Charger": 3.2
})
```

---

## 4. Hardware Implementation (ESP32 / MicroPython)

- **ESP32 (Arduino C++)**: See [`esp32_example.ino`](file:///d:/openADR/openadr3_competition/team_kit/esp32_example.ino). Includes a built-in timer loop reporting every 15 minutes to respect rate limits.
- **MicroPython (Raspberry Pi Pico W / ESP32)**: See [`micropython_example.py`](file:///d:/openADR/openadr3_competition/team_kit/micropython_example.py).

---

## 5. Analyzing Tariffs with Pandas

```python
import pandas as pd

df = pd.read_csv("tariffs_received.csv")
print("Average Tariff : INR", df["price_per_kwh"].mean())
print("Peak Tariff    : INR", df["price_per_kwh"].max())

cheapest_4_hours = df.sort_values(by="price_per_kwh").head(4)
print("\nCheapest 4 hours to charge / pre-cool:")
print(cheapest_4_hours[["start_time", "price_per_kwh"]])
```

---

## 6. OpenADR 3.0 API Summary

| Action | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| 2-Day Ahead Tariffs | GET | `/api/v3/tariffs.json?token=KEY` | 48 hourly intervals (Today + Tomorrow) |
| List Registered Appliances | GET | `/api/v3/appliances?token=KEY` | Returns all team appliances & last kW readings |
| Submit Telemetry | POST | `/api/v3/reports?token=KEY` | Ingests single or multi-appliance power |
| Live Standings | GET | `/api/v3/leaderboard` | View rankings across the authorized teams |
