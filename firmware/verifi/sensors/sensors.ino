// Combined: Breath (flex, A1) + GSR (A0) + MAX30102 (I2C)
// Human-readable serial output at 10 Hz

#include "DFRobot_BloodOxygen_S.h"

#define I2C_ADDRESS 0x57
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, I2C_ADDRESS);

const int FLEX_PIN = A1;   // breathing belt
const int GSR_PIN  = A0;
const unsigned long SAMPLE_INTERVAL_US = 100000; // 10 Hz
unsigned long lastSample = 0;
unsigned long lastHrPoll = 0;

// HR/SpO2 cache
int   lastHR   = 0;
int   lastSPO2 = 0;
float lastTemp = 0.0;

// HR/SpO2 polling. The module recalculates on its own roughly every 4 s;
// polling every 1 s picks up each new value within a second instead of up to 4 s late.
const unsigned long HR_POLL_MS = 1000;

// HR smoothing: average of the last 2 valid readings only, so heart rate can
// still react inside the backend's 10 s question window.
const int NUM_HR_SAMPLES = 2;
int hrReadings[NUM_HR_SAMPLES];
int hrIndex = 0;
int hrCount = 0;  // valid readings so far (up to NUM_HR_SAMPLES), so empty slots don't count as 0

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

    // HR/SpO2/temp every HR_POLL_MS, non-blocking
    if (millis() - lastHrPoll >= HR_POLL_MS) {
      lastHrPoll = millis();
      MAX30102.getHeartbeatSPO2();
      int rawHR = MAX30102._sHeartbeatSPO2.Heartbeat;
      lastSPO2  = MAX30102._sHeartbeatSPO2.SPO2;
      lastTemp  = MAX30102.getTemperature_C();

      if (rawHR > 0) {
        hrReadings[hrIndex] = rawHR;
        hrIndex = (hrIndex + 1) % NUM_HR_SAMPLES;
        if (hrCount < NUM_HR_SAMPLES) hrCount++;
        long sum = 0;
        for (int i = 0; i < hrCount; i++) sum += hrReadings[i];
        lastHR = sum / hrCount;
      }
    }

    // Format parsed by backend/serial_io.py (LINE_RE). Change both together.
    Serial.print("breath: ");      Serial.print(flex);
    Serial.print(", gsr: ");       Serial.print(gsr);
    Serial.print(", heartbeat: "); Serial.print(lastHR);
    Serial.print(", spo2: ");      Serial.print(lastSPO2);  // blood oxygen %, not blood pressure
    Serial.print(", temp: ");      Serial.println(lastTemp, 1);
  }
}