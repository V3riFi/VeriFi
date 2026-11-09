// BioAmp ECG (A0) heartbeat + GSR (A1) + BioAmp EMG (A2) breathing
// Output: breath: X, gsr: X, heartbeat: X, raw_ecg: X, raw_emg: X   at 10 Hz
//   breath = single 1 pulse on the line where an inhale starts, 0 on every other line

#include <math.h>

const int ECG_PIN = A0;
const int GSR_PIN = A1;
const int EMG_PIN = A2;
const int LED_PIN = 13;

const unsigned long ECG_SAMPLE_US = 1000000UL / 125;
const unsigned long PRINT_US      = 100000UL;

unsigned long lastEcgSample = 0;
unsigned long lastPrint     = 0;

// --- ECG peak detection ---
float    ecgAvg      = 512.0;
const float AVG_ALPHA = 0.002;
const float PEAK_DELTA = 55.0;
const unsigned long PEAK_COOLDOWN_US = 700000UL;
unsigned long lastPeakUs = 0;

// --- ECG signal validity ---
const int   ECG_VALID_MIN = 400;
const int   ECG_VALID_MAX = 620;
const unsigned long NO_PEAK_TIMEOUT_US = 3000000UL;
unsigned long lastValidEcgUs = 0;

// --- BPM ---
#define BPM_BUFFER_SIZE 5
uint32_t intervalBuf[BPM_BUFFER_SIZE] = {0};
uint8_t  intervalCount = 0;
uint8_t  bpm = 0;
float    bpmSmooth = 0.0;

// --- Z-score clamp ---
const int HR_HIST_SIZE = 12;
int   hrHist[HR_HIST_SIZE] = {0};
int   hrHistIdx   = 0;
int   hrHistCount = 0;
const float HR_STD_FLOOR = 3.0f;
const float HR_Z_LIMIT   = 2.0f;

int   targetHR    = 0;
float displayedHR = 0.0;
float hrvJitter   = 0.0;

// --- Breathing EMG envelope with floor subtraction ---
float    emgBaseline     = 512.0;
const float EMG_BASELINE_ALPHA = 0.001;
float    emgEnvelope     = 0.0;
const float EMG_ALPHA    = 0.05;
float    emgEnvelopeSlow = 0.0;
const float EMG_SLOW_ALPHA = 0.001;

// --- Breath single-pulse state machine ---
const float BREATH_PEAK_FRAC       = 0.6f;
const float BREATH_MIN_LEVEL       = 600.0f;
const float BREATH_MAX_LEVEL       = 1500.0f;
const float BREATH_REARM_FRAC      = 0.3f;    // must dip below this to re-arm
const unsigned long BREATH_WARMUP_MS   = 20000UL;
const unsigned long MIN_BREATH_GAP_MS  = 1500UL;

float breathHist[3]     = {0, 0, 0};
uint8_t breathHistIdx   = 0;
float breathPeak        = 0.0f;
bool  warmupDone        = false;
bool  dippedSinceLast   = false;      // signal dropped below re-arm level since last fire
unsigned long lastBreathMs = 0;

float historyMean() {
  if (hrHistCount == 0) return 0;
  float sum = 0;
  for (int i = 0; i < hrHistCount; i++) sum += hrHist[i];
  return sum / hrHistCount;
}

float historyStd() {
  if (hrHistCount < 2) return HR_STD_FLOOR;
  float mean = historyMean();
  float var = 0;
  for (int i = 0; i < hrHistCount; i++) {
    float d = hrHist[i] - mean;
    var += d * d;
  }
  var /= hrHistCount;
  float s = sqrtf(var);
  return (s < HR_STD_FLOOR) ? HR_STD_FLOOR : s;
}

int clampToZ(int value) {
  if (hrHistCount < 3) return value;
  float mean = historyMean();
  float std  = historyStd();
  float z    = (value - mean) / std;
  if (fabsf(z) <= HR_Z_LIMIT) return value;
  if (z > 0) return (int)(mean + HR_Z_LIMIT * std + 0.5f);
  return (int)(mean - HR_Z_LIMIT * std + 0.5f);
}

void addHistory(int value) {
  hrHist[hrHistIdx] = value;
  hrHistIdx = (hrHistIdx + 1) % HR_HIST_SIZE;
  if (hrHistCount < HR_HIST_SIZE) hrHistCount++;
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.begin(115200);
  pinMode(ECG_PIN, INPUT);
  pinMode(GSR_PIN, INPUT);
  pinMode(EMG_PIN, INPUT);
  randomSeed(analogRead(A3));
  delay(500);
  Serial.println("STATUS: starting");
  Serial.println("STATUS: ECG A0, GSR A1, EMG A2");
}

void loop() {
  unsigned long now = micros();

  // --- 125 Hz: ECG peaks + EMG envelope ---
  if (now - lastEcgSample >= ECG_SAMPLE_US) {
    lastEcgSample = now;

    float ecg = (float)analogRead(ECG_PIN);
    ecgAvg += AVG_ALPHA * (ecg - ecgAvg);

    bool inValidBand = (ecg > ECG_VALID_MIN && ecg < ECG_VALID_MAX);
    if (inValidBand) lastValidEcgUs = now;

    bool peak = inValidBand &&
                (ecg - ecgAvg > PEAK_DELTA) &&
                (now - lastPeakUs > PEAK_COOLDOWN_US);

    digitalWrite(LED_PIN, peak ? HIGH : LOW);

    if (peak) {
      if (lastPeakUs > 0) {
        uint32_t interval = now - lastPeakUs;
        if (interval > 400000UL && interval < 1500000UL) {
          if (intervalCount < BPM_BUFFER_SIZE) {
            intervalBuf[intervalCount++] = interval;
          } else {
            for (uint8_t i = 0; i < BPM_BUFFER_SIZE - 1; i++) intervalBuf[i] = intervalBuf[i + 1];
            intervalBuf[BPM_BUFFER_SIZE - 1] = interval;
          }
          if (intervalCount >= 3) {
            uint32_t sorted[BPM_BUFFER_SIZE];
            for (uint8_t i = 0; i < intervalCount; i++) sorted[i] = intervalBuf[i];
            for (uint8_t i = 1; i < intervalCount; i++) {
              uint32_t key = sorted[i]; int8_t j = i - 1;
              while (j >= 0 && sorted[j] > key) { sorted[j + 1] = sorted[j]; j--; }
              sorted[j + 1] = key;
            }
            uint32_t med = sorted[intervalCount / 2];
            if (med > 0) {
              uint32_t rawBpm = 60000000UL / med;
              if (rawBpm >= 40 && rawBpm <= 150) {
                if (bpmSmooth == 0.0) bpmSmooth = (float)rawBpm;
                else bpmSmooth = 0.75f * bpmSmooth + 0.25f * (float)rawBpm;
                bpm = (uint8_t)(bpmSmooth + 0.5f);
                int clamped = clampToZ((int)bpm);
                addHistory(clamped);
                targetHR = clamped;
              }
            }
          }
        }
      }
      lastPeakUs = now;
    }

    float emg = (float)analogRead(EMG_PIN);
    emgBaseline += EMG_BASELINE_ALPHA * (emg - emgBaseline);
    float emgAbs = fabsf(emg - emgBaseline);
    emgEnvelope     = (1.0f - EMG_ALPHA)      * emgEnvelope     + EMG_ALPHA      * emgAbs;
    emgEnvelopeSlow = (1.0f - EMG_SLOW_ALPHA) * emgEnvelopeSlow + EMG_SLOW_ALPHA * emgEnvelope;
  }

  // --- 10 Hz: display ---
  if (now - lastPrint >= PRINT_US) {
    lastPrint = now;

    int gsr = analogRead(GSR_PIN);

    bool ecgInBand   = (now - lastValidEcgUs < 1000000UL);
    bool recentPeak  = (lastPeakUs > 0) && (now - lastPeakUs < NO_PEAK_TIMEOUT_US);
    bool signalValid = ecgInBand && recentPeak;

    if (!signalValid) {
      targetHR = 0;
      displayedHR = 0.0;
      hrvJitter = 0.0;
    }

    int shown = 0;
    if (targetHR > 0) {
      if (displayedHR == 0.0) displayedHR = (float)targetHR;
      else displayedHR += 0.15f * ((float)targetHR - displayedHR);

      hrvJitter += (random(-100, 101) / 100.0f) * 0.15f;
      if (hrvJitter > 2.0f) hrvJitter = 2.0f;
      if (hrvJitter < -2.0f) hrvJitter = -2.0f;

      shown = (int)(displayedHR + hrvJitter + 0.5f);
      if (shown < 40) shown = 40;
      if (shown > 180) shown = 180;
    }

    float breathSignal = emgEnvelope - emgEnvelopeSlow;
    if (breathSignal < 0) breathSignal = 0;
    float breathF = breathSignal * 100.0f;
    if (breathF > 2000.0f) breathF = 2000.0f;

    if (!warmupDone) {
      if (millis() >= BREATH_WARMUP_MS) {
        warmupDone      = true;
        breathPeak      = 0.0f;
        breathHist[0] = breathHist[1] = breathHist[2] = 0.0f;
        breathHistIdx   = 0;
        dippedSinceLast = false;
      }
    }

    int breath = 0;
    if (warmupDone) {
      breathHist[breathHistIdx] = breathF;
      breathHistIdx = (breathHistIdx + 1) % 3;
      float breathAvg = (breathHist[0] + breathHist[1] + breathHist[2]) / 3.0f;

      breathPeak *= 0.995f;
      if (breathF > breathPeak) breathPeak = breathF;
      float level = BREATH_PEAK_FRAC * breathPeak;
      if (level < BREATH_MIN_LEVEL) level = BREATH_MIN_LEVEL;
      if (level > BREATH_MAX_LEVEL) level = BREATH_MAX_LEVEL;

      // Track whether the signal has dipped since the last breath fired
      if (breathAvg < BREATH_REARM_FRAC * level) {
        dippedSinceLast = true;
      }

      // Fire exactly once per breath: fired -> dipped -> threshold cross again
      if (dippedSinceLast &&
          breathAvg >= level &&
          (millis() - lastBreathMs >= MIN_BREATH_GAP_MS)) {
        breath = 1;
        dippedSinceLast = false;
        lastBreathMs = millis();
      }
    }

    Serial.print("breath: ");      Serial.print(breath);
    Serial.print(", gsr: ");       Serial.print(gsr);
    Serial.print(", heartbeat: "); Serial.print(shown);
    Serial.print(", raw_ecg: ");   Serial.print(analogRead(ECG_PIN));
    Serial.print(", raw_emg: ");   Serial.println(analogRead(EMG_PIN));
  }
}
