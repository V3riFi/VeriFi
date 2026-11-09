// The dashboard's dropdowns, in order. Each signal below sits in one of them.
export const GROUPS = [
  { key: 'body', label: 'Body sensors' },
  { key: 'face', label: 'Vision' },
  { key: 'voice', label: 'Voice' },
]

// Every signal the dashboard knows about, in the order the tiles show them.
// `field` is the key in "sample" messages (README contract) and in the
// baseline's mean/std. `direction` says which way counts as a reaction:
// "up" flags values above the normal range, "down" below it, "both" either side.
export const SIGNALS = [
  { key: 'gsr', field: 'gsr', label: 'Skin conductance (simulated)', unit: 'raw', direction: 'up', minSpan: 8, source: 'gsr', group: 'body' },
  { key: 'hr', field: 'hr', label: 'Heart rate', unit: 'bpm', direction: 'up', minSpan: 16, source: 'pulse', group: 'body' },
  // Each breath the Arduino counts adds to the rate; the chart and tile both show breaths per minute.
  { key: 'breathing', field: 'breath_rate', label: 'Breathing', unit: 'per min', direction: 'both', minSpan: 8, source: 'breathing', group: 'body' },
  { key: 'blink', field: 'blink_rate', label: 'Blink rate', unit: 'per min', direction: 'both', minSpan: 8, source: 'face', group: 'face' },
  { key: 'hand', field: 'hand_on_face', label: 'Hand on face', unit: '', direction: 'up', boolean: true, source: 'face', group: 'face' },
  { key: 'lip', field: 'lip_compression', label: 'Lip compression', unit: '', direction: 'up', minSpan: 0.4, source: 'face', group: 'face' },
  // Voice values (except loudness) are null while the subject is quiet, so these charts only fill in during answers.
  // Normal ranges come from the voice baseline, where the subject reads sentences aloud.
  { key: 'pitch', field: 'pitch', label: 'Voice pitch', unit: 'Hz', direction: 'up', minSpan: 40, source: 'voice', group: 'voice' },
  { key: 'hnr', field: 'hnr', label: 'Voice clarity (HNR)', unit: 'dB', direction: 'down', minSpan: 6, source: 'voice', group: 'voice' },
  { key: 'jitter', field: 'jitter', label: 'Jitter', unit: '%', direction: 'up', minSpan: 1, source: 'voice', group: 'voice' },
  { key: 'shimmer', field: 'shimmer', label: 'Shimmer', unit: '%', direction: 'up', minSpan: 4, source: 'voice', group: 'voice' },
  // Loudness always has a value (the room), but its normal range is the subject's speaking voice,
  // so it's only compared with baseline while they speak (speechOnly).
  { key: 'loudness', field: 'voice_db', label: 'Loudness', unit: 'dB', direction: 'up', minSpan: 10, speechOnly: true, source: 'voice', group: 'voice' },
  { key: 'slope', field: 'spectral_slope', label: 'Spectral slope', unit: 'dB', direction: 'down', minSpan: 6, source: 'voice', group: 'voice' },
]

export const STRESS = { key: 'stress', field: 'stress', label: 'Combined stress', unit: '/ 100', direction: 'up' }

export const SAMPLE_FIELDS = [...new Set([...SIGNALS.flatMap((s) => [s.field, s.tileField ?? s.field]), 'stress'])]

// Normal range and alert lines for one signal in the current session, or null
// before the baseline exists. Values past `upper` or `lower` are drawn red;
// either is null when that direction doesn't count as a reaction.
export function thresholdsFor(signal, baseline) {
  if (!baseline) return null
  if (signal.key === 'stress') {
    return { mean: 0, std: null, rangeLow: 0, rangeHigh: baseline.stressAlert, upper: baseline.stressAlert, lower: null }
  }
  const mean = baseline.mean[signal.field]
  const std = baseline.std[signal.field]
  if (!Number.isFinite(mean) || !Number.isFinite(std)) return null
  const rangeLow = mean - baseline.alertSd * std
  const rangeHigh = mean + baseline.alertSd * std
  return {
    mean,
    std,
    rangeLow,
    rangeHigh,
    upper: signal.direction === 'down' ? null : rangeHigh,
    lower: signal.direction === 'up' ? null : rangeLow,
  }
}

// "above", "below" or null for a value against those thresholds.
export function alertState(value, thresholds) {
  if (!thresholds || !Number.isFinite(value)) return null
  if (thresholds.upper !== null && value > thresholds.upper) return 'above'
  if (thresholds.lower !== null && value < thresholds.lower) return 'below'
  return null
}
