// The backend, simulated in the browser, for the online demo build only
// (`npm run build:demo`). It is a JavaScript copy of backend/server.py's
// FakeSignals and session flow and backend/scoring.py, speaking the same
// WebSocket messages, so the dashboard runs unchanged. If the Python scoring
// changes, update this copy too. The real dashboard never loads this file.

const SAMPLE_HZ = 20
export const DEMO_BASELINE_S = 20 // the real backend uses 60
const QUESTION_EVERY_S = [10, 16] // the demo asks questions itself, standing in for the button
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
      blink_rate: 17,
    }
  }
}

/* ---------- server.py: Session ---------- */

class Session {
  constructor(subject, now) {
    this.subject = subject
    this.started = now
    this.baselineEnd = now + DEMO_BASELINE_S
    this.baseline = null
    this.samples = []
    this.stress = null
    this.questions = []
    this.nextQuestionAt = null
  }

  ask(now) {
    const label = `Q${this.questions.length + 1}`
    this.questions.push({ label, t: now, spike: null, signals: {}, done: false })
    return { type: 'question', t: round(now, 3), label, phase: 'start' }
  }

  score(q) {
    const window = this.samples.filter((s) => s.t >= q.t - PRE_S - HOLD_RANGE_S && s.t <= q.t + WINDOW_S)
    ;[q.spike, q.signals] = response(window, q.t, this.baseline)
    q.done = true
    const now = this.samples.length ? this.samples[this.samples.length - 1].t : q.t
    return { type: 'response', t: round(now, 3), label: q.label, spike: q.spike, signals: q.signals }
  }

  onSample(sample) {
    const t = sample.t
    this.samples.push(sample)
    const events = []

    if (this.baseline === null && t >= this.baselineEnd) {
      this.baseline = new Baseline(this.samples.filter((s) => s.t >= this.started))
      this.nextQuestionAt = t + 3
      events.push({ type: 'baseline', t: round(t, 3), phase: 'done', ...this.baseline.asMessage() })
    }

    if (this.baseline !== null) {
      const recent = this.samples.filter((s) => s.t > t - STRESS_WINDOW_S)
      const raw = stress(recent, this.baseline)
      if (raw !== null) {
        this.stress = this.stress === null ? raw : this.stress + STRESS_SMOOTHING * (raw - this.stress)
        sample.stress = Math.round(this.stress)
      }
    }

    for (const q of this.questions) {
      if (!q.done && t >= q.t + WINDOW_S) events.push(this.score(q))
    }
    return events
  }

  stop() {
    const events = this.baseline ? this.questions.filter((q) => !q.done).map((q) => this.score(q)) : []
    const ranked = [...this.questions].sort((a, b) => (b.spike ?? -Infinity) - (a.spike ?? -Infinity))
    events.push({ type: 'summary', questions: ranked.map((q) => ({ label: q.label, spike: q.spike })) })
    return events
  }

  statusEntry() {
    return {
      subject: this.subject,
      started: round(this.started, 3),
      baseline_end: round(this.baselineEnd, 3),
      baseline: this.baseline ? this.baseline.asMessage() : null,
      questions: this.questions.map((q) => ({ label: q.label, t: round(q.t, 3), spike: q.spike, signals: q.signals })),
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
      emit(session.ask(sample.t))
      fake.react(sample.t)
      session.nextQuestionAt = sample.t + uniform(...QUESTION_EVERY_S)
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
        } else if (message.type === 'stop') {
          if (!session) return emit({ type: 'error', message: 'No session running.' })
          const events = session.stop()
          session = null
          events.forEach(emit)
        }
      }, 0)
      return true
    },
  }
}
