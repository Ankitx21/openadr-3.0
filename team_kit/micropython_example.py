"""MicroPython Client for OpenADR 3.0 Grid-Interactive Competition.

Target Hardware: Raspberry Pi Pico W, ESP32, ESP8266 running MicroPython.
Requires: network, urequests, ujson (built into MicroPython standard builds).
"""

import time
import network
import ujson
import urequests

# 1. WiFi Configuration
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

# 2. Server & Assigned Team Token
SERVER_URL = "https://your-competition-server.onrender.com"  # or http://192.168.1.100:7860
TEAM_TOKEN = "YOUR_ASSIGNED_TOKEN"
APPLIANCE_NAME = "Smart_Water_Heater"


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        while not wlan.isconnected():
            time.sleep(0.5)
    print("WiFi connected! IP:", wlan.ifconfig()[0])


def fetch_tariffs():
    url = f"{SERVER_URL}/api/v3/tariffs.json"
    print("[VTN] Fetching 48-Hour Day-Ahead Tariffs...")
    resp = urequests.get(url, headers={"Authorization": "Bearer " + TEAM_TOKEN})
    if resp.status_code == 200:
        data = resp.json()
        tariffs = data.get("tariffs", [])
        print(f"[VTN] Received {len(tariffs)} hourly intervals.")
        if tariffs:
            print("First Hour Price: INR", tariffs[0]["price_per_kwh"])
        resp.close()
        return tariffs
    else:
        print("[ERROR] Failed to fetch tariffs:", resp.status_code)
        resp.close()
        return []


def report_power(power_kw, status="RUNNING"):
    url = f"{SERVER_URL}/api/v3/baseline"
    payload = {
        "appliance": APPLIANCE_NAME,
        "energy": power_kw * 0.25,  # Example assumes constant average kW over 15 minutes.
        # Real hardware must integrate measured power over the entire interval.
        "status": status,
    }
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + TEAM_TOKEN}
    print(f"[VTN] Reporting {APPLIANCE_NAME}: {power_kw} kW...")
    resp = urequests.post(url, data=ujson.dumps(payload), headers=headers)
    if resp.status_code in (200, 201):
        print("[SUCCESS] Telemetry accepted by VTN server!")
    else:
        print("[ERROR] Telemetry rejected:", resp.status_code)
    resp.close()


def main():
    connect_wifi()
    tariffs = fetch_tariffs()
    
    # Telemetry loop: report power periodically (e.g. every 15 minutes)
    while True:
        measured_kw = 1.85  # Replace with sensor reading (ADC, CT sensor, or smart plug)
        # Sleep 15 minutes (900s) to respect rate limits and conserve bandwidth
        time.sleep(900)
        report_power(measured_kw, status="GRID_OPTIMIZED")


if __name__ == "__main__":
    main()
