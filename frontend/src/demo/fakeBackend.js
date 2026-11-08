// The backend, simulated in the browser, for the online demo build only
// (`npm run build:demo`). It is a JavaScript copy of backend/server.py's
// FakeSignals and session flow, backend/scoring.py, and the per-answer verdict
// (backend/confidence.py + backend/verdict.py), speaking the same WebSocket
// messages, so the dashboard runs unchanged. If the Python scoring changes,
// update this copy too. The real dashboard never loads this file.

const SAMPLE_HZ = 20
export const DEMO_BASELINE_S = 30 // the real backend uses 60; at least 30 gives the verdicts enough calm moments
const QUESTION_EVERY_S = [14, 18] // the demo asks questions itself; each answer's window closes before the next
const STRESS_SMOOTHING = 0.1

// scoring.py
const PRE_S = 2
const WINDOW_S = 10
const MIN_STD = { gsr: 1, hr: 1, flex: 1, breath_rate: 0.5, blink_rate: 1, lip_compression: 0.05 }
const SPIKE_WEIGHTS = { gsr: 0.6, hr: 0.25, breath_hold: 0.15 }
const STRESS_WEIGHTS = { gsr: 0.45, hr: 0.25, breath_rate: 0.15, blink_rate: 0.1, lip_compression: 0.05 }
const STRESS_TWO_SIDED = new Set(['breath_rate', 'blink_rate'])
const STRESS_WINDOW_S = 2
const STRESS_FULL_SCALE_SD = 8
const ALERT_SD = 2
const STRESS_ALERT = Math.round((ALERT_SD / STRESS_FULL_SCALE_SD) * 100)
const HOLD_RANGE_S = 1.5
const HOLD_FRACTION = 0.3
const NORMAL_PAUSE_S = 1

const round = (value, places) => Math.round(value * 10 ** places) / 10 ** places

function gauss(sd) {
  const u = 1 - Math.random()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random()) * sd
}

const uniform = (lo, hi) => lo + Math.random() * (hi - lo)

/* ---------- scoring.py ---------- */

function column(samples, key) {
  return samples.map((s) => {
    const v = s[key]
    if (v === null || v === undefined || v < 0 || (key === 'hr' && v === 0)) return NaN
    return v
  })
}

const finite = (xs) => xs.filter((x) => !Number.isNaN(x))
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length
const std = (xs) => {
  const m = mean(xs)
  return Math.sqrt(mean(xs.map((x) => (x - m) ** 2)))
}

function rollingRange(t, x, span) {
  const out = new Array(x.length).fill(NaN)
  let start = 0
  for (let i = 0; i < x.length; i++) {
    while (t[start] < t[i] - span) start++
    const window = finite(x.slice(start, i + 1))
    if (window.length >= 2) out[i] = Math.max(...window) - Math.min(...window)
  }
  return out
}

class Baseline {
  constructor(samples) {
    this.mean = {}
    this.std = {}
    for (const [key, minStd] of Object.entries(MIN_STD)) {
      const x = finite(column(samples, key))
      if (x.length >= 2) {
        this.mean[key] = mean(x)
        this.std[key] = Math.max(std(x), minStd)
      }
    }
    const ranges = finite(rollingRange(column(samples, 't'), column(samples, 'flex'), HOLD_RANGE_S)).sort((a, b) => a - b)
    this.breathRange = ranges.length ? ranges[Math.floor(ranges.length / 2)] : null
  }

  z(key, value) {
    return (value - this.mean[key]) / this.std[key]
  }

  asMessage() {
    const rounded = (obj) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, round(v, 3)]))
    return { mean: rounded(this.mean), std: rounded(this.std), alert_sd: ALERT_SD, stress_alert: STRESS_ALERT }
  }
}

function stress(recent, baseline) {
  let weightedSum = 0
  let totalWeight = 0
  for (const [key, weight] of Object.entries(STRESS_WEIGHTS)) {
    if (!(key in baseline.mean)) continue
    const x = finite(column(recent, key))
    if (!x.length) continue
    const z = baseline.z(key, mean(x))
    const distance = STRESS_TWO_SIDED.has(key) ? Math.abs(z) : Math.max(z, 0)
    const effective = weight * Math.max(1, distance)
    weightedSum += effective * distance
    totalWeight += effective
  }
  if (totalWeight === 0) return null
  return Math.min(100, Math.max(0, (weightedSum / totalWeight / STRESS_FULL_SCALE_SD) * 100))
}

function weighted(parts, weights) {
  const used = Object.keys(weights).filter((k) => k in parts)
  if (!used.length) return null
  return used.reduce((sum, k) => sum + parts[k] * weights[k], 0) / used.reduce((sum, k) => sum + weights[k], 0)
}

function longestHold(t, flex, inWindow, baseline) {
  if (!baseline.breathRange) return null
  const ranges = rollingRange(t, flex, HOLD_RANGE_S)
  if (ranges.every((r, i) => !inWindow[i] || Number.isNaN(r))) return null
  let longest = 0
  let runStart = null
  for (let i = 0; i < t.length; i++) {
    if (inWindow[i] && ranges[i] < HOLD_FRACTION * baseline.breathRange) {
      runStart ??= t[i]
      longest = Math.max(longest, t[i] - runStart)
    } else {
      runStart = null
    }
  }
  return longest > 0 ? longest + HOLD_RANGE_S : 0
}

function response(samples, qT, baseline) {
  const t = column(samples, 't')
  const pre = t.map((x) => x >= qT - PRE_S && x < qT)
  const post = t.map((x) => x >= qT && x <= qT + WINDOW_S)
  const signals = {}
  const split = (key) => {
    const x = column(samples, key)
    return [finite(x.filter((_, i) => pre[i])), finite(x.filter((_, i) => post[i]))]
  }

  for (const key of ['gsr', 'hr']) {
    if (!(key in baseline.mean)) continue
    const [before, after] = split(key)
    if (before.length && after.length) signals[key] = round((Math.max(...after) - mean(before)) / baseline.std[key], 2)
  }
  if ('blink_rate' in baseline.mean) {
    const [before, after] = split('blink_rate')
    if (before.length && after.length) signals.blink = round((mean(after) - mean(before)) / baseline.std.blink_rate, 2)
  }
  const hold = longestHold(t, column(samples, 'flex'), post, baseline)
  if (hold !== null) signals.breath_hold_s = round(hold, 1)

  const parts = Object.fromEntries(['gsr', 'hr'].filter((k) => k in signals).map((k) => [k, signals[k]]))
  if (hold !== null) parts.breath_hold = Math.max(0, hold - NORMAL_PAUSE_S)
  const spike = weighted(parts, SPIKE_WEIGHTS)
  return [spike === null ? null : round(spike, 2), signals]
}

/* ---------- confidence.py + verdict.py ---------- */
// The per-answer verdict. confidence.py scores each answer against the baseline; verdict.py turns
// the combined Z into a deception likelihood. The demo has no microphone or camera, so Voice and
// Lip / hand have no data, and every remaining category has one signal: its confidence is that
// signal's p (one-sided when directional, two-sided otherwise), as in confidence.py.

const CATEGORIES = [
  { name: 'Skin', weight: 0.3, sensors: [{ key: 'gsr', name: 'Skin conductance', direction: 1, phasic: [1, 5] }] },
  { name: 'Breathing', weight: 0.2, sensors: [{ key: 'chest_range', name: 'Chest movement', direction: -1 }] },
  { name: 'Cardiac', weight: 0.15, sensors: [{ key: 'hr', name: 'Heart rate', direction: 0 }] },
  { name: 'Voice', weight: 0.13, sensors: [], missing: 'Pitch: no microphone in the online demo' },
  { name: 'Blink', weight: 0.12, sensors: [{ key: 'blink_rate', name: 'Blink rate', direction: 0 }] },
  { name: 'Lip / hand', weight: 0.05, sensors: [], missing: 'Lip compression: no camera in the online demo' },
]
const CONF_MIN_STD = { gsr: 1, hr: 1, chest_range: 1, blink_rate: 3 }
const MIN_WINDOW_SAMPLES = 3
const EPS = 1e-300

// verdict.py constants
const LAMBDA = 2
const SIGMA = 1
const PRIOR = 0.5
const INTERVAL_Z = 1.2816
const Z_MIN = -LAMBDA / SIGMA ** 2
const BANDS = [
  ['high', 0.85],
  ['elevated', 0.6],
  ['unclear', 0.3],
  ['low', 0],
]
const RESULT = { high: 'reaction', elevated: 'reaction', unclear: 'unclear', low: 'calm', insufficient: 'insufficient' }
const MIN_CATEGORIES = 2
const MIN_COVERAGE = 0.8
const MAX_GAP_S = 2
const PSEUDO_STEP_S = 1
const NULL_EDGES = Array.from({ length: 17 }, (_, i) => -4 + i * 0.5)
const TRACE_S = 10
const TRACE_STEP_S = 0.25
const TRACE_KEYS = [
  ['gsr', 'Skin conductance', 'raw'],
  ['hr', 'Heart rate', 'bpm'],
  ['flex', 'Breathing belt', 'raw'],
  ['blink_rate', 'Blink rate', 'per min'],
]
const PROGRESS_EVERY_S = 0.3

// Complementary error function (Numerical Recipes erfcc, relative error < 1.2e-7).
function erfc(x) {
  const z = Math.abs(x)
  const t = 1 / (1 + 0.5 * z)
  const r =
    t *
    Math.exp(
      -z * z -
        1.26551223 +
        t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))),
    )
  return x >= 0 ? r : 2 - r
}

const normSf = (z) => 0.5 * erfc(z / Math.SQRT2)

// Inverse of the standard normal CDF (Acklam's algorithm).
function normInv(p) {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239]
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572]
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783]
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
  const tail = (q) => (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
  if (p < 0.02425) return tail(Math.sqrt(-2 * Math.log(p)))
  if (p > 1 - 0.02425) return -tail(Math.sqrt(-2 * Math.log(1 - p)))
  const q = p - 0.5
  const r = q * q
  return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
}

const normIsf = (p) => -normInv(Math.min(1 - 1e-16, Math.max(EPS, p)))

function sampleStats(xs) {
  const m = mean(xs)
  return { mu: m, sigma: Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1)) }
}

// Samples with start <= t <= end. Samples are in time order, so a binary search finds the start.
function between(samples, start, end) {
  let lo = 0
  let hi = samples.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (samples[mid].t < start) lo = mid + 1
    else hi = mid
  }
  const out = []
  for (let i = lo; i < samples.length && samples[i].t <= end; i++) out.push(samples[i])
  return out
}

// One signal's values with start <= t <= end. chest_range is the belt's range over HOLD_RANGE_S.
function signalValues(samples, key, start = -Infinity, end = Infinity) {
  const t = column(samples, 't')
  const x = key === 'chest_range' ? rollingRange(t, column(samples, 'flex'), HOLD_RANGE_S) : column(samples, key)
  return x.filter((v, i) => !Number.isNaN(v) && t[i] >= start && t[i] <= end)
}

function confBaselines(silent) {
  const out = {}
  for (const cat of CATEGORIES) {
    for (const s of cat.sensors) {
      const x = signalValues(silent, s.key)
      if (x.length < 2) continue
      const st = sampleStats(x)
      out[s.key] = { mu: st.mu, sigma: Math.max(st.sigma, CONF_MIN_STD[s.key] ?? 0), n: x.length }
    }
  }
  return out
}

// confidence.score_question: per category z and confidence, combined with weighted Stouffer.
function scoreQuestion(samples, base, qT, until, qEnd) {
  const nearby = between(samples, qT - Math.max(PRE_S, HOLD_RANGE_S), until)
  const categories = {}
  const skipped = []
  for (const cat of CATEGORIES) {
    if (!cat.sensors.length) {
      skipped.push([cat.name, cat.missing])
      continue
    }
    const s = cat.sensors[0]
    const b = base[s.key]
    if (!b) {
      skipped.push([cat.name, `${s.name}: no baseline`])
      continue
    }
    let reading
    if (s.phasic) {
      const anchor = qEnd ?? qT
      const pre = signalValues(nearby, s.key, qT - PRE_S, qT - 1e-6)
      const post = signalValues(nearby, s.key, qT + s.phasic[0], Math.min(anchor + s.phasic[1], until))
      if (!pre.length || post.length < MIN_WINDOW_SAMPLES) {
        skipped.push([cat.name, `${s.name}: not enough data around the question`])
        continue
      }
      reading = { mu: Math.max(...post), sigma: 0, baseMu: mean(pre), n: post.length }
    } else {
      const x = signalValues(nearby, s.key, qT, until)
      if (x.length < MIN_WINDOW_SAMPLES) {
        skipped.push([cat.name, `${s.name}: window has ${x.length} values (needs ${MIN_WINDOW_SAMPLES})`])
        continue
      }
      reading = { ...sampleStats(x), baseMu: b.mu, n: x.length }
    }
    const diff = (reading.mu - reading.baseMu) / Math.sqrt(b.sigma ** 2 + reading.sigma ** 2)
    const z = s.direction ? s.direction * diff : Math.abs(diff)
    const p = s.direction ? normSf(z) : 2 * normSf(z)
    categories[cat.name] = {
      weight: cat.weight,
      method: s.direction ? 'stouffer' : 'chi2',
      confidence: p,
      z: normIsf(p),
      signal: {
        key: s.key,
        name: s.name,
        weight: 1,
        direction: s.direction,
        phasic: Boolean(s.phasic),
        base: round(reading.baseMu, 3),
        base_sd: round(b.sigma, 3),
        now: round(reading.mu, 3),
        now_sd: round(reading.sigma, 3),
        z: round(z, 3),
        p: round(p, 4),
        n: reading.n,
      },
    }
  }
  const cats = Object.values(categories)
  const norm = Math.sqrt(cats.reduce((sum, c) => sum + c.weight ** 2, 0))
  const zTotal = cats.length ? cats.reduce((sum, c) => sum + c.weight * c.z, 0) / norm : null
  return { categories, skipped, zTotal, norm }
}

const logNormal = (x, m, v) => -0.5 * (x - m) ** 2 / v - 0.5 * Math.log(v) - 0.5 * Math.log(2 * Math.PI)
const logLr = (z) => {
  const zz = Math.max(z, Z_MIN)
  return logNormal(zz, LAMBDA, 1 + SIGMA ** 2) - logNormal(zz, 0, 1)
}
const posterior = (total) => 1 / (1 + Math.exp(-(Math.log(PRIOR / (1 - PRIOR)) + total)))
const bandOf = (p) => (p === null ? 'insufficient' : BANDS.find(([, low]) => p >= low)[0])

function histogram(values, value) {
  const counts = new Array(NULL_EDGES.length - 1).fill(0)
  for (const v of values) {
    const x = Math.min(NULL_EDGES[NULL_EDGES.length - 1], Math.max(NULL_EDGES[0], v))
    counts[Math.min(counts.length - 1, Math.floor((x - NULL_EDGES[0]) / 0.5))]++
  }
  const m = mean(values)
  const sd = values.length > 1 ? Math.sqrt(values.reduce((s, x) => s + (x - m) ** 2, 0) / (values.length - 1)) : 0
  const out = { n: values.length, mean: round(m, 3), sd: round(sd, 3), edges: NULL_EDGES, counts }
  if (Number.isFinite(value)) {
    // Share of calm moments below it, ties (within 0.05 SD, verdict.py NULL_TIE_SD) counted half.
    const below = values.filter((v) => v < value - 0.05).length
    const ties = values.filter((v) => Math.abs(v - value) <= 0.05).length
    out.percentile = round((below + 0.5 * ties) / values.length, 3)
  }
  return out
}

// verdict.Engine: the baselines, plus pseudo-questions across the silent baseline (calm moments).
class Engine {
  constructor(samples, start, end) {
    this.base = confBaselines(between(samples, start, end))
    this.ready = Object.keys(this.base).length > 0
    this.null = {}
    for (let t = start + PRE_S; t + WINDOW_S <= end; t += PSEUDO_STEP_S) {
      const r = scoreQuestion(samples, this.base, t, t + WINDOW_S, null)
      for (const [name, c] of Object.entries(r.categories)) (this.null[name] ??= []).push(c.z)
      if (r.zTotal !== null) (this.null._total ??= []).push(r.zTotal)
    }
  }

  nullSummary(name, value) {
    const values = this.null[name]
    return values?.length ? histogram(values, value) : null
  }
}

function coverage(samples, start, end) {
  const times = between(samples, start, end).map((s) => s.t)
  if (times.length < 2) return [0, end - start]
  const points = [start, ...times, end]
  let gap = 0
  for (let i = 1; i < points.length; i++) gap = Math.max(gap, points[i] - points[i - 1])
  return [(times[times.length - 1] - times[0]) / (end - start), gap]
}

function gates(engine, raw, samples, qT, until, provisional) {
  const out = [{ key: 'baseline', label: 'Calm baseline recorded', pass: Boolean(engine?.ready), detail: engine?.ready ? 'Baseline analyzed' : 'No usable baseline to compare with' }]
  const gsr = between(samples, qT - PRE_S, until).map((s) => s.gsr).filter(Number.isFinite)
  let skin
  if (!gsr.length) skin = [false, 'No skin conductance data']
  else if (Math.max(...gsr) <= 0) skin = [false, 'Skin sensor reads 0: it is not touching the skin']
  else if (!raw?.categories.Skin) skin = [false, 'Skin conductance unusable']
  else skin = [true, 'Skin conductance measured']
  out.push({ key: 'skin', label: 'Skin sensor reading', pass: skin[0], detail: skin[1] })
  const [share, gap] = coverage(samples, qT, until)
  out.push({
    key: 'coverage',
    label: 'Sensors kept streaming',
    pass: provisional ? gap <= MAX_GAP_S : share >= MIN_COVERAGE && gap <= MAX_GAP_S,
    detail: provisional ? `Longest gap ${gap.toFixed(1)} s so far` : `${Math.round(share * 100)}% of the window covered, longest gap ${gap.toFixed(1)} s`,
  })
  const usable = Object.keys(raw?.categories ?? {})
  out.push({ key: 'signals', label: `At least ${MIN_CATEGORIES} signal groups`, pass: usable.length >= MIN_CATEGORIES, detail: `${usable.length} usable: ${usable.join(', ') || 'none'}` })
  return out
}

function traceSlice(samples, qT, qEnd, until) {
  const near = between(samples, qT - TRACE_S, Math.min(until, qT + TRACE_S))
  const series = {}
  for (const [key, label, unit] of TRACE_KEYS) {
    const bins = new Map()
    for (const s of near) {
      if (!Number.isFinite(s[key])) continue
      const bin = Math.floor((s.t - qT + TRACE_S) / TRACE_STEP_S)
      const entry = bins.get(bin) ?? { t: 0, v: 0, n: 0 }
      entry.t += s.t - qT
      entry.v += s[key]
      entry.n++
      bins.set(bin, entry)
    }
    const points = [...bins.values()].map((e) => [round(e.t / e.n, 2), round(e.v / e.n, 3)])
    if (points.length) series[key] = { label, unit, points }
  }
  const anchor = (qEnd ?? qT) - qT
  const windows = [
    { key: 'pre', label: 'Reference: just before the question', from: -PRE_S, to: 0 },
    { key: 'peak', label: 'Skin reaction window', from: 1, to: round(Math.min(anchor + 5, until - qT), 2) },
    { key: 'answer', label: 'Answer window', from: 0, to: round(until - qT, 2) },
  ]
  return { from: -TRACE_S, to: TRACE_S, series, windows, end: qEnd === null ? null : round(qEnd - qT, 2) }
}

// verdict.score: the verdict object the dashboard reads (README: response.verdict).
function verdictFor(engine, samples, qT, until, qEnd, provisional) {
  const raw = engine?.ready ? scoreQuestion(samples, engine.base, qT, until, qEnd) : null
  const checks = gates(engine, raw, samples, qT, until, provisional)
  const failed = checks.find((g) => !g.pass)
  const z = raw?.zTotal ?? null
  const categories = CATEGORIES.map((cat) => {
    const r = raw?.categories[cat.name]
    const missing = (raw?.skipped ?? []).filter(([name]) => name === cat.name).map(([, why]) => why)
    if (!r) return { name: cat.name, weight: cat.weight, missing, z: null, contribution: null, suspicion: null }
    const entry = {
      name: cat.name,
      weight: cat.weight,
      missing,
      z: round(r.z, 3),
      contribution: round((r.weight * r.z) / raw.norm, 3),
      suspicion: round(1 - r.confidence, 4),
      method: r.method,
    }
    if (!provisional) {
      entry.signals = [r.signal]
      entry.null = engine.nullSummary(cat.name, r.z)
    }
    return entry
  })
  const v = {
    provisional,
    categories,
    gates: checks,
    z: z === null ? null : round(z, 3),
    norm: raw ? round(raw.norm, 3) : null,
    model: { lambda: LAMBDA, sigma: SIGMA, prior: PRIOR, interval_z: INTERVAL_Z },
  }
  if (failed || z === null) {
    Object.assign(v, { band: 'insufficient', result: RESULT.insufficient, likelihood: null, interval: null, reason: failed?.detail ?? 'No signal group could be scored', log_lr: null, z_eff: null })
  } else {
    const lr = logLr(z)
    const likelihood = posterior(lr)
    const band = bandOf(likelihood)
    Object.assign(v, {
      band,
      result: RESULT[band],
      likelihood: round(likelihood, 4),
      interval: [round(posterior(logLr(z - INTERVAL_Z)), 4), round(posterior(logLr(z + INTERVAL_Z)), 4)],
      reason: null,
      log_lr: round(lr, 4),
      z_eff: round(Math.max(z, Z_MIN), 3),
    })
  }
  if (!provisional) {
    v.null_total = engine?.ready ? engine.nullSummary('_total', z) : null
    v.trace = traceSlice(samples, qT, qEnd, until)
  }
  return v
}

// verdict.session_summary: every scored answer's log LR added up.
function sessionSummary(answers) {
  let total = 0
  let loTotal = 0
  let hiTotal = 0
  const zs = []
  const counts = { high: 0, elevated: 0, unclear: 0, low: 0, insufficient: 0 }
  const history = answers.map(([label, v]) => {
    counts[v.band]++
    if (v.log_lr !== null) {
      total += v.log_lr
      loTotal += logLr(v.z - INTERVAL_Z)
      hiTotal += logLr(v.z + INTERVAL_Z)
      zs.push(v.z)
    }
    return {
      label,
      band: v.band,
      log_lr: v.log_lr,
      cum_log_lr: round(total, 4),
      likelihood: zs.length ? round(posterior(total), 4) : null,
      interval: zs.length ? [round(posterior(loTotal), 4), round(posterior(hiTotal), 4)] : null,
    }
  })
  if (!zs.length) return { likelihood: null, interval: null, band: 'insufficient', log_lr: null, z: null, answers: 0, history, counts }
  const likelihood = posterior(total)
  return {
    likelihood: round(likelihood, 4),
    band: bandOf(likelihood),
    interval: [round(posterior(loTotal), 4), round(posterior(hiTotal), 4)],
    log_lr: round(total, 4),
    z: round(zs.reduce((a, b) => a + b, 0) / Math.sqrt(zs.length), 3),
    answers: zs.length,
    history,
    counts,
  }
}

/* ---------- server.py: FakeSignals ---------- */

class FakeSignals {
  constructor() {
    this.gsr = 450
    this.t0 = Date.now() / 1000
    this.gsrRises = []
    this.hrBumpUntil = 0
    this.hold = null
    this.holdFlex = 330
  }

  react(now) {
    if (Math.random() < 0.5) return
    const size = uniform(0.5, 1.5)
    this.gsrRises.push([now + uniform(1.5, 3), 40 * size])
    this.hrBumpUntil = now + 8
    if (Math.random() < 0.5) {
      const start = now + uniform(0.5, 2)
      this.hold = [start, start + uniform(2.5, 4)]
    }
  }

  sample() {
    const now = Date.now() / 1000
    const elapsed = now - this.t0
    const period = 1 / SAMPLE_HZ

    this.gsr += gauss(0.8)
    this.gsr += (450 - this.gsr) * 0.002
    for (const [start, rise] of this.gsrRises) {
      if (start <= now && now < start + 3) this.gsr += (rise * period) / 3
    }
    this.gsrRises = this.gsrRises.filter(([start]) => now < start + 3)

    let hr = 72 + 4 * Math.sin(elapsed / 7) + gauss(0.5)
    if (now < this.hrBumpUntil) hr += 8

    let flex = 330 + 25 * Math.sin(2 * Math.PI * 0.25 * elapsed)
    if (this.hold && this.hold[0] <= now && now < this.hold[1]) flex = this.holdFlex
    else this.holdFlex = flex
    flex += gauss(1)

    return {
      type: 'sample',
      t: round(now, 3),
      gsr: round(this.gsr, 1),
      hr: round(hr, 1),
      flex: round(flex, 1),
      breath_rate: 15,
      // Varies a little, like a real blink rate (a flat signal reads as exactly calm).
      blink_rate: round(17 + 2 * Math.sin(elapsed / 11) + gauss(0.8), 1),
    }
  }
}

/* ---------- server.py: Session ---------- */

// Stand-ins for the interrogator's questions, so the demo's answers have text.
const DEMO_QUESTIONS = [
  'Where were you on Tuesday night?',
  'Have you been to the warehouse on 5th Street?',
  'Do you know who owns the blue van?',
  'Did you speak to anyone after 10 pm?',
  'Have you ever seen this knife before?',
  'Is your name what you told us?',
]

class Session {
  constructor(subject, now) {
    this.subject = subject
    this.started = now
    this.baselineEnd = now + DEMO_BASELINE_S
    this.baseline = null
    this.engine = null // verdict.Engine, built when the baseline is done
    this.verdict = null // every scored answer so far, combined
    this.samples = []
    this.stress = null
    this.questions = []
    this.nextQuestionAt = null
  }

  ask(now, text = null) {
    const label = `Q${this.questions.length + 1}`
    const due = now + WINDOW_S
    this.questions.push({ label, text, t: now, end: null, due, spike: null, signals: {}, verdict: null, progressT: 0, done: false })
    return { type: 'question', t: round(now, 3), label, text, due: round(due, 3), phase: 'start' }
  }

  // "Done asking": the answer gets WINDOW_S from here. The demo has no microphone, so no latency.
  endQuestion(now) {
    const q = this.questions.findLast((x) => !x.done && x.end === null)
    if (!q) return null
    q.end = now
    q.due = Math.max(q.due, now + WINDOW_S)
    return { type: 'question', t: round(now, 3), label: q.label, due: round(q.due, 3), phase: 'end' }
  }

  score(q, now) {
    const window = between(this.samples, q.t - PRE_S - HOLD_RANGE_S, q.t + WINDOW_S)
    ;[q.spike, q.signals] = response(window, q.t, this.baseline)
    q.verdict = verdictFor(this.engine, this.samples, q.t, now, q.end, false)
    q.done = true
    return { type: 'response', t: round(now, 3), label: q.label, spike: q.spike, signals: q.signals, voice: null, verdict: q.verdict }
  }

  sessionMessage(now) {
    this.verdict = sessionSummary(this.questions.filter((q) => q.done && q.verdict).map((q) => [q.label, q.verdict]))
    return { type: 'session_verdict', t: round(now, 3), ...this.verdict }
  }

  onSample(sample) {
    const t = sample.t
    this.samples.push(sample)
    const events = []

    if (this.baseline === null && t >= this.baselineEnd) {
      this.baseline = new Baseline(this.samples.filter((s) => s.t >= this.started))
      this.engine = new Engine(this.samples, this.started, this.baselineEnd)
      this.nextQuestionAt = t + 3
      events.push({ type: 'baseline', t: round(t, 3), phase: 'done', ...this.baseline.asMessage() })
    }

    if (this.baseline !== null) {
      const recent = between(this.samples, t - STRESS_WINDOW_S + 1e-6, t)
      const raw = stress(recent, this.baseline)
      if (raw !== null) {
        this.stress = this.stress === null ? raw : this.stress + STRESS_SMOOTHING * (raw - this.stress)
        sample.stress = Math.round(this.stress)
      }
    }

    let scored = false
    for (const q of this.questions) {
      if (q.done) continue
      if (t >= q.due) {
        events.push(this.score(q, t))
        scored = true
      } else if (t - q.progressT >= PROGRESS_EVERY_S) {
        // The live estimate on the partial window, so the gauge moves.
        q.progressT = t
        const verdict = verdictFor(this.engine, this.samples, q.t, t, q.end, true)
        events.push({ type: 'verdict_progress', t: round(t, 3), label: q.label, elapsed: round(t - q.t, 2), due: round(q.due, 3), verdict })
      }
    }
    if (scored) events.push(this.sessionMessage(t))
    return events
  }

  stop(now) {
    const events = this.baseline ? this.questions.filter((q) => !q.done).map((q) => this.score(q, now)) : []
    if (events.length) events.push(this.sessionMessage(now))
    const ranked = [...this.questions].sort((a, b) => (b.spike ?? -Infinity) - (a.spike ?? -Infinity))
    events.push({
      type: 'summary',
      questions: ranked.map((q) => ({ label: q.label, spike: q.spike, likelihood: q.verdict?.likelihood ?? null, band: q.verdict?.band ?? null })),
      session: this.verdict,
    })
    return events
  }

  statusEntry() {
    return {
      subject: this.subject,
      started: round(this.started, 3),
      baseline_end: round(this.baselineEnd, 3),
      baseline: this.baseline ? this.baseline.asMessage() : null,
      questions: this.questions.map((q) => ({
        label: q.label,
        text: q.text,
        t: round(q.t, 3),
        end: q.end === null ? null : round(q.end, 3),
        due: round(q.due, 3),
        spike: q.spike,
        signals: q.signals,
        voice: null,
        verdict: q.verdict,
        done: q.done,
      })),
      verdict: this.verdict,
    }
  }
}

/* ---------- the "socket" ---------- */

// Same shape as connectSocket in ../lib/socket.js.
export function connectDemo({ onMessage, onStatus }) {
  const fake = new FakeSignals()
  let session = null
  const emit = (message) => onMessage(message)

  const status = () => ({
    type: 'status',
    components: {
      arduino: 'simulated',
      gsr: 'simulated',
      pulse: 'simulated',
      breathing: 'simulated',
      camera: 'off',
      face: 'simulated',
      voice: 'off',
    },
    session: session ? session.statusEntry() : null,
  })

  setTimeout(() => {
    onStatus('open')
    emit(status())
  }, 0)

  setInterval(() => {
    const sample = fake.sample()
    const events = session ? session.onSample(sample) : []
    emit(sample)
    events.forEach(emit)
    // Stand in for the interrogator's button so the demo shows question markers.
    if (session?.nextQuestionAt && sample.t >= session.nextQuestionAt) {
      emit(session.ask(sample.t, DEMO_QUESTIONS[session.questions.length % DEMO_QUESTIONS.length]))
      fake.react(sample.t)
      session.nextQuestionAt = sample.t + uniform(...QUESTION_EVERY_S)
      session.autoEndAt = sample.t + 2 // the automatic question "finishes asking" after 2 s
    }
    if (session?.autoEndAt && sample.t >= session.autoEndAt) {
      session.autoEndAt = null
      const end = session.endQuestion(sample.t)
      if (end) emit(end)
    }
  }, 1000 / SAMPLE_HZ)

  setInterval(() => emit(status()), 1000)

  return {
    send(message) {
      const now = Date.now() / 1000
      setTimeout(() => {
        if (message.type === 'start') {
          if (session) return emit({ type: 'error', message: 'A session is already running. Send stop first.' })
          session = new Session(String(message.subject || 'subject'), now)
          emit({ type: 'baseline', t: round(now, 3), phase: 'start', duration: DEMO_BASELINE_S })
        } else if (message.type === 'question') {
          if (!session) return emit({ type: 'error', message: 'No session running. Send start first.' })
          if (session.baseline === null) {
            return emit({ type: 'error', message: 'Still recording the baseline. Wait for it to finish.' })
          }
          session.nextQuestionAt = null // the operator is asking now; stop the automatic questions
          session.autoEndAt = null
          const text = typeof message.text === 'string' && message.text.trim() ? message.text.trim() : null
          emit(session.ask(now, text))
          fake.react(now)
        } else if (message.type === 'question_end') {
          if (!session) return emit({ type: 'error', message: 'No session running.' })
          const end = session.endQuestion(now)
          emit(end ?? { type: 'error', message: 'No question is waiting for Done asking.' })
        } else if (message.type === 'stop') {
          if (!session) return emit({ type: 'error', message: 'No session running.' })
          const events = session.stop(now)
          session = null
          events.forEach(emit)
        }
      }, 0)
      return true
    },
  }
}
