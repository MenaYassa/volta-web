/*
 * Voltra Local — ESP-01 relay driver for TONLY MTTL-W01
 *
 * Talks the strip's INTERNAL LED-I2C bus (decoded from Hackaday project
 * 202043 captures) and exposes the outlets over plain HTTP, so the
 * volta-web gateway (or any browser) can switch them with no cloud.
 *
 * Hardware (see docs-esp-mod.md — READ IT FIRST, mains voltage inside):
 *   ESP-01 GPIO0 -> Power-board LED-SDA pad (isolated from SoC)
 *   ESP-01 GPIO2 -> Power-board LED-SCL pad (isolated from SoC)
 *   ESP-01 VCC   -> Control-board 3.3V rail (3.3V ONLY - 5V kills it)
 *   ESP-01 GND   -> common ground
 *   I2C device address: 0x54 (7-bit). No extra pull-ups needed on ESP-01
 *   (the bus already has them); if flaky, add 4k7 to 3V3 on SDA/SCL.
 *
 * Flashing (Arduino IDE + ESP8266 core):
 *   Board: "Generic ESP8266 Module", Flash Size: "1M (512K SPIFFS)".
 *   Wire GPIO0->GND for flash mode, CH_PD->3V3, RST floating, TX/RX to a
 *   3.3V USB-serial adapter (NOT 5V). Unplug GPIO0 from GND to run.
 *
 * API:
 *   GET /                    mini control page (4 buttons)
 *   GET /api/outlet?n=1&s=on|off   switch outlet (1-4), replies {"ok":true,...}
 *   GET /api/state           {"1":"on|off",...} (assumed state; latching
 *                            relays remember through power loss, so press
 *                            "resync" in the UI after manual button use)
 *
 * Frames: coil pulse, 100 ms gap, clear (matches reference captures).
 */
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <Wire.h>

#define SDA_PIN 0        // GPIO0
#define SCL_PIN 2        // GPIO2
#define I2C_ADDR 0x54    // 7-bit (0xA8 write on the wire)

const char* WIFI_SSID = "Patko";
const char* WIFI_PASS = "Merna@KiKo#2017";  // your home Wi-Fi; change if needed
const char* HOSTNAME  = "tonly-strip";

// [relay][byte] — packet 0 = coil pulse, packet 1 = clear (100 ms apart)
static const uint8_t R_ON[4][5] = {
  {0x13,0x1A,0x1A,0x00,0x00},
  {0x13,0x26,0x0A,0x01,0x00},
  {0x13,0x29,0x0A,0x04,0x00},
  {0x13,0x2A,0x09,0x10,0x00},
};
static const uint8_t R_ON_CLR[4][5] = {
  {0x13,0x1A,0x06,0x00,0x00},
  {0x13,0x26,0x06,0x00,0x00},
  {0x13,0x29,0x06,0x00,0x00},
  {0x13,0x2A,0x05,0x00,0x00},
};
static const uint8_t R_OFF[4][5] = {
  {0x13,0x2A,0x26,0x00,0x00},
  {0x13,0x2A,0x06,0x02,0x00},
  {0x13,0x2A,0x06,0x08,0x00},
  {0x13,0x2A,0x06,0x20,0x00},
};
static const uint8_t R_OFF_CLR[4][5] = {
  {0x13,0x2A,0x0A,0x00,0x00},
  {0x13,0x2A,0x0A,0x00,0x00},
  {0x13,0x2A,0x0A,0x00,0x00},
  {0x13,0x2A,0x0A,0x00,0x00},
};

ESP8266WebServer server(80);
bool outletState[4] = {false, false, false, false};  // assumed; resync if unsure

static void pulse(const uint8_t f[5]) {
  Wire.beginTransmission(I2C_ADDR);
  for (uint8_t i = 0; i < 5; i++) Wire.write(f[i]);
  Wire.endTransmission();
}

static void setRelay(uint8_t n, bool on) {  // n = 0..3
  if (n > 3) return;
  pulse(on ? R_ON[n] : R_OFF[n]);
  delay(100);
  pulse(on ? R_ON_CLR[n] : R_OFF_CLR[n]);
  outletState[n] = on;
}

static String stateJson() {
  String j = "{";
  for (uint8_t i = 0; i < 4; i++) {
    if (i) j += ",";
    j += "\"" + String(i + 1) + "\":\"" + (outletState[i] ? "on" : "off") + "\"";
  }
  return j + "}";
}

static const char PAGE[] PROGMEM = R"HTML(
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TONLY strip</title>
<style>body{font-family:system-ui;background:#0f172a;color:#fff;max-width:420px;margin:0 auto;padding:20px}
button{font-size:18px;padding:14px;margin:6px 4px;border-radius:10px;border:0;min-width:120px}.on{background:#16a34a;color:#fff}.off{background:#475569;color:#fff}</style>
</head><body><h2>TONLY strip</h2><div id="o"></div>
<script>async function r(){const s=await(await fetch('/api/state')).json();let h='';
for(let i=1;i<=4;i++){const on=s[i]==='on';
h+=`<div>Outlet ${i}: <b>${s[i].toUpperCase()}</b><br><button class="on" onclick="go(${i},'on')">ON</button><button class="off" onclick="go(${i},'off')">OFF</button></div>`;}
document.getElementById('o').innerHTML=h;}
async function go(n,s){await fetch(`/api/outlet?n=${n}&s=${s}`);r();}r();setInterval(r,5000);</script></body></html>)HTML";

void setup() {
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  WiFi.mode(WIFI_STA);
  WiFi.hostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint8_t tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 100) delay(200);

  server.on("/", []() { server.send_P(200, "text/html", PAGE); });
  server.on("/api/state", []() {
    server.send(200, "application/json", stateJson());
  });
  server.on("/api/outlet", []() {
    int n = server.hasArg("n") ? server.arg("n").toInt() : 0;
    String s = server.hasArg("s") ? server.arg("s") : "";
    if (n < 1 || n > 4 || (s != "on" && s != "off")) {
      server.send(400, "application/json", "{\"error\":\"use ?n=1..4&s=on|off\"}");
      return;
    }
    setRelay(n - 1, s == "on");
    server.send(200, "application/json",
                "{\"ok\":true,\"outlet\":" + String(n) + ",\"state\":\"" + s + "\"}");
  });
  server.onNotFound([]() {
    server.send(404, "application/json", "{\"error\":\"not found\"}");
  });
  server.begin();
}

void loop() {
  server.handleClient();
}
