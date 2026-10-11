/*
  CANIS - WiFi connect diagnostic (ESP32)
  ---------------------------------------
  We know "<HOME_SSID>" is visible, WPA2, strong (-67 dBm) - yet connect times out.
  This sketch tries to connect with a WiFi EVENT HANDLER that prints the exact
  reason the AP rejected/dropped us. That reason code tells us the real cause.

  Reason codes you might see (printed on disconnect):
    15  HANDSHAKE_TIMEOUT  -> wrong password (the 4-way handshake failed)
    2   AUTH_EXPIRE        -> auth problem, often password or PMF/802.11w
    201 NO_AP_FOUND        -> can't find AP at connect time (band/range/power)
    205 CONNECTION_FAIL    -> generic assoc failure, often POWER (bad USB cable)
    8   ASSOC_LEAVE        -> we left / got kicked

  Also hardened against the usual gremlins: erases stale creds, disables modem
  sleep, disables persistent storage. Serial Monitor @ 115200.
*/

#include <WiFi.h>

const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.println("  [evt] associated to AP (link up, waiting for IP)");
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.print("  [evt] GOT IP: "); Serial.println(WiFi.localIP());
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      Serial.print("  [evt] DISCONNECTED - reason code = ");
      Serial.println(info.wifi_sta_disconnected.reason);
      break;
    default:
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nWiFi connect diagnostic");

  WiFi.persistent(false);          // don't reuse stale stored creds
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);     // erase any old config
  delay(200);
  WiFi.setSleep(false);            // no modem sleep during connect

  Serial.print("Connecting to "); Serial.println(WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void loop() {
  static unsigned long lastPrint = 0;
  static int attempts = 0;

  if (millis() - lastPrint >= 1000) {
    lastPrint = millis();
    wl_status_t s = WiFi.status();
    Serial.print("  status = "); Serial.print(s);
    Serial.print(s == WL_CONNECTED ? "  CONNECTED!" :
                 s == WL_NO_SSID_AVAIL ? "  (no SSID found)" :
                 s == WL_CONNECT_FAILED ? "  (connect failed)" :
                 s == WL_DISCONNECTED ? "  (disconnected/trying)" :
                 s == WL_IDLE_STATUS ? "  (idle)" : "");
    Serial.println();

    if (s == WL_CONNECTED) {
      Serial.print(">>> SUCCESS. IP = "); Serial.println(WiFi.localIP());
      Serial.print(">>> RSSI = "); Serial.println(WiFi.RSSI());
      while (true) delay(1000);    // done - leave it connected
    }

    // every 10s, re-kick the connection in case it's just stuck
    if (++attempts % 10 == 0) {
      Serial.println("  ...re-trying WiFi.begin()");
      WiFi.disconnect();
      delay(100);
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
  }
}
