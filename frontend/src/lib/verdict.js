// Per-answer verdicts (backend/verdict.py, README: response.verdict): the bands, checks on
// what the backend sends, and the words the dashboard uses. Never "truth" or "lie": a
// deception likelihood, against this person's own calm baseline.

// Checked top-down, same cutoffs as backend/verdict.py BANDS.
export const BANDS = [
  {
    key: 'high',
    label: 'High',
    min: 0.85,
    glyph: '▲▲',
    plain: 'A strong reaction compared with their calm baseline.',
  },
  {
    key: 'elevated',
    label: 'Elevated',
    min: 0.6,
    glyph: '▲',
    plain: 'A clear reaction compared with their calm baseline.',
  },
  {
    key: 'unclear',
    label: 'Unclear',
    min: 0.3,
    glyph: '◆',
    plain: 'Some change, but not enough to lean either way.',
  },
  { key: 'low', label: 'Low', min: 0, glyph: '▼', plain: 'Little change from their calm baseline.' },
]
export const INSUFFICIENT = {
  key: 'insufficient',
  label: 'Not enough signal',
  glyph: '–',
  plain: 'The sensors did not give enough to judge this answer.',
}
export const BAND_KEYS = [...BANDS.map((b) => b.key), INSUFFICIENT.key]

export const bandInfo = (key) => BANDS.find((b) => b.key === key) ?? INSUFFICIENT

const finite = (value) => (Number.isFinite(value) ? value : null)
const text = (value) => (typeof value === 'string' && value ? value : null)

function interval(value) {
  if (!Array.isArray(value) || value.length !== 2) return null
  const [lo, hi] = value.map(finite)
  return lo === null || hi === null ? null : [Math.min(lo, hi), Math.max(lo, hi)]
}

function nullDistribution(value) {
  if (!value || !Array.isArray(value.edges) || !Array.isArray(value.counts)) return null
  if (value.edges.length !== value.counts.length + 1) return null
  return {
    n: finite(value.n) ?? 0,
    mean: finite(value.mean),
    sd: finite(value.sd),
    edges: value.edges.map(Number),
    counts: value.counts.map((c) => (Number.isFinite(c) ? c : 0)),
    percentile: finite(value.percentile),
  }
}

function signal(value) {
  if (!value || typeof value !== 'object') return null
  return {
    key: String(value.key ?? ''),
    name: String(value.name ?? value.key ?? ''),
    weight: finite(value.weight) ?? 0,
    direction: [1, -1].includes(value.direction) ? value.direction : 0,
    phasic: Boolean(value.phasic),
    delta: Boolean(value.delta), // compared with the seconds just before the question
    base: finite(value.base),
    baseSd: finite(value.base_sd),
    now: finite(value.now),
    nowSd: finite(value.now_sd),
    z: finite(value.z),
    p: finite(value.p),
  }
}

function category(value) {
  if (!value || typeof value !== 'object' || !text(value.name)) return null
  return {
    name: value.name,
    weight: finite(value.weight) ?? 0,
    z: finite(value.z),
    contribution: finite(value.contribution),
    suspicion: finite(value.suspicion),
    method: text(value.method),
    missing: Array.isArray(value.missing) ? value.missing.map(String) : [],
    signals: Array.isArray(value.signals) ? value.signals.map(signal).filter(Boolean) : null,
    null: nullDistribution(value.null),
  }
}

function trace(value) {
  if (!value || typeof value !== 'object' || !value.series || typeof value.series !== 'object') return null
  const series = Object.entries(value.series)
    .filter(([, s]) => s && Array.isArray(s.points) && s.points.length > 1)
    .map(([key, s]) => ({
      key,
      label: String(s.label ?? key),
      unit: String(s.unit ?? ''),
      points: s.points.filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])),
    }))
  const windows = Array.isArray(value.windows)
    ? value.windows.filter((w) => w && Number.isFinite(w.from) && Number.isFinite(w.to))
    : []
  return { from: finite(value.from) ?? -10, to: finite(value.to) ?? 10, series, windows, end: finite(value.end) }
}

function gate(value) {
  if (!value || typeof value !== 'object') return null
  return { key: String(value.key ?? ''), label: String(value.label ?? ''), pass: Boolean(value.pass), detail: text(value.detail) }
}

// A verdict object from the backend, checked and normalized; null if it isn't one.
export function toVerdict(value) {
  if (!value || typeof value !== 'object' || !BAND_KEYS.includes(value.band)) return null
  const likelihood = finite(value.likelihood)
  const band = likelihood === null ? INSUFFICIENT.key : value.band
  return {
    band,
    result: text(value.result),
    likelihood,
    interval: interval(value.interval),
    reason: text(value.reason),
    provisional: Boolean(value.provisional),
    z: finite(value.z),
    zEff: finite(value.z_eff),
    logLr: finite(value.log_lr),
    norm: finite(value.norm),
    model: value.model && typeof value.model === 'object' ? value.model : null,
    categories: Array.isArray(value.categories) ? value.categories.map(category).filter(Boolean) : [],
    gates: Array.isArray(value.gates) ? value.gates.map(gate).filter(Boolean) : [],
    nullTotal: nullDistribution(value.null_total),
    trace: trace(value.trace),
  }
}

// The session-level verdict ("session_verdict", or session.verdict in "status").
export function toSessionVerdict(value) {
  if (!value || typeof value !== 'object' || !BAND_KEYS.includes(value.band)) return null
  return {
    band: finite(value.likelihood) === null ? INSUFFICIENT.key : value.band,
    likelihood: finite(value.likelihood),
    interval: interval(value.interval),
    logLr: finite(value.log_lr),
    z: finite(value.z),
    answers: finite(value.answers) ?? 0,
    history: Array.isArray(value.history)
      ? value.history
          .filter((h) => h && typeof h === 'object')
          .map((h) => ({
            label: String(h.label ?? ''),
            band: BAND_KEYS.includes(h.band) ? h.band : INSUFFICIENT.key,
            logLr: finite(h.log_lr),
            cumLogLr: finite(h.cum_log_lr),
            likelihood: finite(h.likelihood),
            interval: interval(h.interval),
          }))
      : [],
  }
}

// Whole percent, never a hard 0 or 100.
export function percent(value) {
  if (!Number.isFinite(value)) return '–'
  const p = value * 100
  if (p >= 99.5) return '>99'
  if (p < 0.5) return '<1'
  return String(Math.round(p))
}

export function signed(value, digits = 1) {
  if (!Number.isFinite(value)) return '–'
  const fixed = Math.abs(value).toFixed(digits)
  if (Number(fixed) === 0) return fixed
  return `${value > 0 ? '+' : '−'}${fixed}`
}

// Counts of each band over a session's answered questions.
export function bandCounts(questions) {
  const counts = Object.fromEntries(BAND_KEYS.map((key) => [key, 0]))
  for (const q of questions) if (q.verdict) counts[q.verdict.band]++
  return counts
}
