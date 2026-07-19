/*
  CANIS — WiFi diagnostic (ESP32)
  -------------------------------
  Not part of the robot. Flash this temporarily to find out WHY the ESP32 can't
  join "<HOME_SSID>". It scans every 2.4 GHz network the ESP32 can see and prints the
  name, signal strength (RSSI), and security type.

  Read the output:
    - "<HOME_SSID>" NOT in the list      -> ESP32 can't see it (5 GHz only / out of range)
    - "<HOME_SSID>" listed, RSSI < -80   -> too weak, move ESP32 closer to the router
    - "<HOME_SSID>" listed as WPA3        -> ESP32 likely can't join; switch router to WPA2
    - "<HOME_SSID>" listed, WPA2, strong  -> it's a PASSWORD problem in the real sketch

  Serial Monitor @ 115200.
*/

#include <WiFi.h>

void setup() {
  Serial.begin(115200);
  delay(300);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);
  Serial.println("\nWiFi scan starting...");
}

const char* enc(wifi_auth_mode_t m) {
  switch (m) {
    case WIFI_AUTH_OPEN:            return "OPEN";
    case WIFI_AUTH_WEP:             return "WEP";
    case WIFI_AUTH_WPA_PSK:         return "WPA";
    case WIFI_AUTH_WPA2_PSK:        return "WPA2";
    case WIFI_AUTH_WPA_WPA2_PSK:    return "WPA/WPA2";
    case WIFI_AUTH_WPA2_ENTERPRISE: return "WPA2-ENT";
    case WIFI_AUTH_WPA3_PSK:        return "WPA3";
    case WIFI_AUTH_WPA2_WPA3_PSK:   return "WPA2/WPA3";
    default:                        return "?";
  }
}

void loop() {
  int n = WiFi.scanNetworks();
  Serial.print("\nFound "); Serial.print(n); Serial.println(" networks:");
  for (int i = 0; i < n; i++) {
    Serial.printf("  %2d) %-24s  RSSI %4d dBm   %s\n",
                  i + 1, WiFi.SSID(i).c_str(), WiFi.RSSI(i), enc(WiFi.encryptionType(i)));
  }
  WiFi.scanDelete();
  delay(5000);   // rescan every 5s
}
