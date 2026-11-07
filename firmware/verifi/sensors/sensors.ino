// Combined: Breath (flex, A1) + GSR (A0) + MAX30102 (I2C, DFRobot library)
// Output: breath: X, gsr: X, heartbeat: X, spo2: X, temp: X   at 10 Hz

#include "DFRobot_BloodOxygen_S.h"

#define I2C_ADDRESS 0x57
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, I2C_ADDRESS);

const int FLEX_PIN = A1;
const int GSR_PIN  = A0;
const unsigned long SAMPLE_INTERVAL_US = 100000; // 10 Hz
unsigned long lastSample = 0;
unsigned long lastHrPoll = 0;

// Just the last values the sensor reported — no averaging
int   lastHR   = 0;
int   lastSPO2 = 0;
float lastTemp = 0.0;

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("STATUS: starting");

  unsigned long start = millis();
  bool ok = false;
  while (millis() - start < 5000) {
    if (MAX30102.begin()) { ok = true; break; }
    delay(100);
  }
  if (ok) {
    MAX30102.sensorStartCollect();
    Serial.println("STATUS: MAX30102 ready");
  } else {
    Serial.println("STATUS: MAX30102 init failed - HR/SpO2 will read 0");
  }
}

void loop() {
  unsigned long now = micros();
  if (now - lastSample >= SAMPLE_INTERVAL_US) {
    lastSample = now;

    int flex = analogRead(FLEX_PIN);
    int gsr  = analogRead(GSR_PIN);

    // HR/SpO2 every 4 s
    if (millis() - lastHrPoll >= 4000) {
      lastHrPoll = millis();
      MAX30102.getHeartbeatSPO2();

      int rawHR = MAX30102._sHeartbeatSPO2.Heartbeat;
      if (rawHR > 0) {
        lastHR = rawHR;              // <-- direct assignment, no averaging
      }
      lastSPO2 = MAX30102._sHeartbeatSPO2.SPO2;
      lastTemp = MAX30102.getTemperature_C();
    }

    Serial.print("breath: ");      Serial.print(flex);
    Serial.print(", gsr: ");       Serial.print(gsr);
    Serial.print(", heartbeat: "); Serial.print(lastHR);
    Serial.print(", spo2: ");      Serial.print(lastSPO2);
    Serial.print(", temp: ");      Serial.println(lastTemp, 1);
  }
}
