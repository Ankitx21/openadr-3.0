/*
 * OpenADR 3.0 ESP32 Microcontroller Client
 * Grid-Interactive Appliance Optimization Competition
 *
 * Target Board: ESP32 Dev Module (Arduino IDE)
 * Libraries needed: WiFi.h, HTTPClient.h, ArduinoJson.h
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// 1. WiFi Credentials
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// 2. Competition Server & Assigned Team Token
const char* server_url = "https://your-competition-server.onrender.com"; // or http://192.168.1.100:7860
const char* team_token = "YOUR_ASSIGNED_TOKEN"; // Replace with your team's assigned key

// 3. Hardware Appliance Settings
const char* appliance_name = "Smart_Water_Heater";
const int RELAY_PIN = 23; // Pin connected to relay/SSR controlling appliance

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW); // Start OFF

  Serial.println("\n--- Connecting to WiFi ---");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi Connected! IP Address: " + WiFi.localIP().toString());

  // Step 1: Fetch 48-Hour Day-Ahead Tariffs
  fetchDayAheadTariffs();

  // Step 2: Send Initial Telemetry
  // Wait for a complete measurement interval before reporting.
}

void loop() {
  // Read physical CT current sensor or power meter here
  float measured_kw = 1.75;

  // Report power telemetry every 15 minutes (900000 ms)
  delay(900000);
  reportPowerConsumption(measured_kw, "RUNNING_GRID_INTERACTIVE");
}

// Function to download direct JSON tariffs
void fetchDayAheadTariffs() {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  String url = String(server_url) + "/api/v3/tariffs.json";

  Serial.println("\n[HTTP] Fetching Day-Ahead Tariffs from VTN...");
  http.begin(url);
  http.addHeader("Authorization", String("Bearer ") + team_token);
  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    Serial.println("[HTTP] Tariffs received successfully!");

    // Parse JSON
    DynamicJsonDocument doc(24576);
    deserializeJson(doc, payload);

    JsonArray tariffs = doc["tariffs"];
    Serial.printf("[VTN] Received %d Hourly Intervals:\n", tariffs.size());

    // Check first hour price
    if (tariffs.size() > 0) {
      float first_hour_price = tariffs[0]["price_per_kwh"];
      Serial.printf(" -> Hour 0 Price: INR %.2f / kWh\n", first_hour_price);
    }
  } else {
    Serial.printf("[ERROR] Failed to fetch tariffs. HTTP Code: %d\n", httpCode);
  }
  http.end();
}

// Function to send power telemetry back to Organizer VTN
void reportPowerConsumption(float power_kw, const char* status_note) {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  String url = String(server_url) + "/api/v3/baseline";

  Serial.printf("\n[VTN] Reporting %s (%.2f kW)...\n", appliance_name, power_kw);
  http.begin(url);
  http.addHeader("Authorization", String("Bearer ") + team_token);
  http.addHeader("Content-Type", "application/json");

  // Create JSON payload
  StaticJsonDocument<256> doc;
  doc["appliance"] = appliance_name;
  // Demo assumes constant average power for the full 15 minutes.
  // Real hardware must integrate sensor power over the interval.
  doc["energy"] = power_kw * 0.25;
  doc["status"] = status_note;

  String requestBody;
  serializeJson(doc, requestBody);

  int httpCode = http.POST(requestBody);
  if (httpCode == 201 || httpCode == 200) {
    Serial.println("[SUCCESS] Telemetry accepted by Organizer VTN!");
  } else {
    Serial.printf("[ERROR] Telemetry rejected. HTTP Code: %d\n", httpCode);
  }
  http.end();
}
