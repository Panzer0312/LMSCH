// Spookhub PIR trigger (Arduino-ESP32 core v3)
// PIR (HC-SR501 / AM312) on GPIO13 -> HTTP POST to Spookhub /api/trigger.

#include <WiFi.h>
#include <HTTPClient.h>

// ---- Config ----
const char* WIFI_SSID      = "MyNetwork";
const char* WIFI_PASS      = "supersecret";
// IP instead of spookhub.local: mDNS lookup from the ESP32 is slow/unreliable.
const char* SPOOKHUB_URL   = "http://192.168.1.10:8080/api/trigger";
const char* TRIGGER_TOKEN  = "";  // leave empty if Spookhub has no token set
const int   PIR_PIN        = 13;
const int   LED_PIN        = 2;
// Spookhub enforces the real cooldown; this only avoids spamming the network
// while the PIR output chatters.
const unsigned long LOCAL_COOLDOWN_MS = 3000;
const unsigned long WIFI_RETRY_MS     = 5000;

int lastPir = LOW;
unsigned long lastTriggerMs = 0;
unsigned long lastWifiCheckMs = 0;

void blinkLed() {
  digitalWrite(LED_PIN, HIGH);
  delay(150);
  digitalWrite(LED_PIN, LOW);
}

void sendTrigger() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Trigger skipped: WiFi down");
    return;
  }

  HTTPClient http;
  http.begin(SPOOKHUB_URL);
  // Keep the loop responsive if Spookhub is unreachable.
  http.setConnectTimeout(2000);
  http.setTimeout(2000);
  http.addHeader("Content-Type", "application/json");
  if (TRIGGER_TOKEN[0] != '\0') {
    http.addHeader("X-Trigger-Token", TRIGGER_TOKEN);
  }

  int code = http.POST("{}");
  Serial.printf("Trigger -> HTTP %d\n", code);
  if (code > 0) {
    Serial.println(http.getString());
    if (code >= 200 && code < 300) blinkLed();
  } else {
    Serial.println(http.errorToString(code));
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  WiFi.mode(WIFI_STA);
  // Modem sleep delays the first packet after idle by 100+ ms; we want low latency.
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting WiFi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  Serial.println(WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "WiFi not connected yet");

  // HC-SR501 needs up to ~60 s warm-up; ignore its startup state as an edge.
  lastPir = digitalRead(PIR_PIN);
}

void loop() {
  unsigned long now = millis();

  // Non-blocking so motion detection keeps running while the AP is away.
  if (WiFi.status() != WL_CONNECTED && now - lastWifiCheckMs >= WIFI_RETRY_MS) {
    lastWifiCheckMs = now;
    Serial.println("WiFi lost, reconnecting");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }

  int pir = digitalRead(PIR_PIN);
  // Rising edge only: the PIR holds HIGH for seconds, one trigger per motion.
  if (pir == HIGH && lastPir == LOW && now - lastTriggerMs >= LOCAL_COOLDOWN_MS) {
    lastTriggerMs = now;
    Serial.println("Motion");
    sendTrigger();
  }
  lastPir = pir;

  delay(10);
}
