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

---

## 3. Student Competition Lifecycle (3 Phases)

The competition is structured into 3 distinct phases, with rolling dynamic tariffs continuously available throughout:

### Continuous: Fetching Dynamic Tariffs
Day-Ahead dynamic tariffs (today + tomorrow) are always live and updated continuously on the server:
```bash
# Using Python Client
python openadr3_client.py --token "YOUR_ASSIGNED_TOKEN" --server "https://your-server.onrender.com" --fetch-tariffs

# Or direct HTTP GET
curl -X GET "https://your-server.onrender.com/api/v3/tariffs?token=YOUR_ASSIGNED_TOKEN"
```

### Phase 1: Connection & Data Testing
Students check connectivity and test data transmission with the server. Testing records are stored for reference so organizers can verify your connection:
```bash
python openadr3_client.py --token "YOUR_ASSIGNED_TOKEN" --server "https://your-server.onrender.com" --test --appliance Smart_AC --energy 1.5 --outdoor-temp 30.5
```
Direct HTTP POST:
```bash
curl -X POST "https://your-server.onrender.com/api/v3/testing?token=YOUR_ASSIGNED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"appliance": "Smart_AC", "energy": 1.5, "outdoor_temperature": 30.5}'
```

### Phase 2: Baseline Data (2-Week Active Period)
During the 2-week active competition period, submit regular baseline energy data. Readings are automatically snapped to 15-minute intervals:
```bash
python openadr3_client.py --token "YOUR_ASSIGNED_TOKEN" --server "https://your-server.onrender.com" --baseline 2.10 --appliance Smart_AC --outdoor-temp 32.0
```
Direct HTTP POST:
```bash
curl -X POST "https://your-server.onrender.com/api/v3/baseline?token=YOUR_ASSIGNED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"appliances": {"Smart_AC": 2.10, "EV_Charger": 3.40}, "outdoor_temperature": 32.0}'
```

### Phase 3: Prototype Product Posting (Actual Ready Product)
When your smart optimization algorithm or hardware is ready, post your tariff-optimized load data:
```bash
python openadr3_client.py --token "YOUR_ASSIGNED_TOKEN" --server "https://your-server.onrender.com" --prototype 1.15 --appliance Smart_AC
```
Direct HTTP POST:
```bash
curl -X POST "https://your-server.onrender.com/api/v3/prototype?token=YOUR_ASSIGNED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"appliances": {"Smart_AC": 1.15, "EV_Charger": 0.0}, "notes": "Shifted load off evening peak"}'
```

---

## 4. Rate Limiting Rules
To maintain server performance and prevent accidental loops from microcontrollers:
1. **Per-Appliance Cooldown**: Minimum **2 seconds** required between submissions for the same appliance.
2. **Team Minute Limit**: Maximum **20 submissions per minute** across all microcontrollers for a team.
3. **Team Hourly Limit**: Maximum **60 submissions per hour** across all microcontrollers for a team.
4. Exceeding limits returns `HTTP 429 Too Many Requests`.

---

## 5. Hardware Implementation (ESP32 / MicroPython)

- **ESP32 (Arduino C++)**: See [`esp32_example.ino`](file:///d:/openADR/openadr_django/team_kit/esp32_example.ino). Includes a 15-minute interval timer loop.
- **MicroPython (Raspberry Pi Pico W / ESP32)**: See [`micropython_example.py`](file:///d:/openADR/openadr_django/team_kit/micropython_example.py).

---

## 6. OpenADR 3.0 API Summary

| Action | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| Dynamic Tariffs | GET | `/api/v3/tariffs?token=KEY` | Continuous 48 hourly intervals (Today + Tomorrow) |
| Phase 1: Testing | POST | `/api/v3/testing?token=KEY` | Verify client-server connection and data sending |
| Phase 2: Baseline Data | POST | `/api/v3/baseline?token=KEY` | 2-week baseline competition data (15-min intervals) |
| Phase 3: Prototype Posting | POST | `/api/v3/prototype?token=KEY` | Post smart tariff-optimized device loads |
| 15-Min Interval Data | GET | `/api/v3/baseline/intervals` | Query interval profiles for charts and analysis |
| List Registered Appliances | GET | `/api/v3/appliances?token=KEY` | Returns team appliances and latest energy readings |
| Live Standings | GET | `/api/v3/leaderboard` | View baseline & prototype progress rankings |
