// Every signal the dashboard knows about, in the order the tiles show them.
// `field` is the key in "sample" messages (README contract) and in the
// baseline's mean/std. `direction` says which way counts as a reaction:
// "up" flags values above the normal range, "both" flags either side.
export const SIGNALS = [
  { key: 'gsr', field: 'gsr', label: 'Skin conductance', unit: 'raw', direction: 'up', minSpan: 40, source: 'gsr' },
  { key: 'hr', field: 'hr', label: 'Heart rate', unit: 'bpm', direction: 'up', minSpan: 16, source: 'pulse' },
  {
    key: 'breathing',
    field: 'flex',
    label: 'Breathing',
    unit: 'raw',
    // The tile shows the rate; the chart shows the belt itself, where a held breath is a flat line.
    tileField: 'breath_rate',
    tileUnit: 'per min',
    direction: 'both',
    minSpan: 80,
    source: 'breathing',
  },
  { key: 'blink', field: 'blink_rate', label: 'Blink rate', unit: 'per min', direction: 'both', minSpan: 8, source: 'face' },
  { key: 'hand', field: 'hand_on_face', label: 'Hand on face', unit: '', direction: 'up', boolean: true, source: 'face' },
  { key: 'lip', field: 'lip_compression', label: 'Lip compression', unit: '', direction: 'up', minSpan: 0.4, source: 'face' },
]

export const STRESS = { key: 'stress', field: 'stress', label: 'Combined stress', unit: '/ 100', direction: 'up' }

export const SAMPLE_FIELDS = [...new Set([...SIGNALS.flatMap((s) => [s.field, s.tileField ?? s.field]), 'stress'])]

// Normal range and alert line for one signal in the current session, or null
// before the baseline exists. Values outside it are drawn red.
export function thresholdsFor(signal, baseline) {
  if (!baseline) return null
  if (signal.key === 'stress') return { mean: 0, upper: baseline.stressAlert, lower: null }
  const mean = baseline.mean[signal.field]
  const std = baseline.std[signal.field]
  if (!Number.isFinite(mean) || !Number.isFinite(std)) return null
  return {
    mean,
    upper: mean + baseline.alertSd * std,
    lower: signal.direction === 'both' ? mean - baseline.alertSd * std : null,
    rangeLow: mean - baseline.alertSd * std,
  }
}

// "above", "below" or null for a value against those thresholds.
export function alertState(value, thresholds) {
  if (!thresholds || !Number.isFinite(value)) return null
  if (value > thresholds.upper) return 'above'
  if (thresholds.lower !== null && value < thresholds.lower) return 'below'
  return null
}
