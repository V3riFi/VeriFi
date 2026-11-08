import { connectSocket } from './socket'
import { connectDemo } from '../demo/fakeBackend'
import { SAMPLE_FIELDS } from './signals'

const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws'
// The online demo (`npm run build:demo`) simulates the backend in the browser.
export const IS_DEMO = import.meta.env.MODE === 'demo'

export const LIVE_WINDOW_S = 60
const STALE_AFTER_MS = 2000
const TILE_REFRESH_MS = 250
const START_TIMEOUT_MS = 4000
const ERROR_SHOW_MS = 8000

const newBuffer = () => Object.fromEntries([['t', []], ...SAMPLE_FIELDS.map((field) => [field, []])])

function toNumber(value) {
  if (typeof value === 'boolean') return value ? 1 : 0
  return Number.isFinite(value) ? value : null
}

function toBaseline(message) {
  if (!message?.mean || !message?.std) return null
  return {
    mean: message.mean,
    std: message.std,
    alertSd: Number.isFinite(message.alert_sd) ? message.alert_sd : 2,
    stressAlert: Number.isFinite(message.stress_alert) ? message.stress_alert : 25,
  }
}

// Samples arrive 20 times a second, so they go into plain arrays that charts
// read each frame. React state (`state`) only changes for things worth re-rendering.
let live = newBuffer()
const recordings = new Map() // session id -> buffer
let dataVersion = 0

let state = {
  connection: 'connecting',
  components: null, // from the backend's "status" message
  latest: null, // most recent sample, refreshed a few times a second
  sampleHz: 0,
  sessions: [], // this tab's sessions, oldest first
  activeId: null,
  viewId: null, // session shown in the charts; null = live view
  starting: false,
  error: null,
}

const listeners = new Set()
const frameListeners = new Set()
let socket = null
let nextSessionId = 1
let pendingName = null
let startTimer = null
let errorTimer = null
let lastTileRefresh = 0
let lastSampleAt = 0
let samplesThisSecond = 0

function setState(patch) {
  state = { ...state, ...patch }
  dataVersion++
  listeners.forEach((listener) => listener())
}

const activeSession = () => state.sessions.find((s) => s.id === state.activeId) ?? null

function updateSession(id, patch) {
  setState({ sessions: state.sessions.map((s) => (s.id === id ? { ...s, ...patch } : s)) })
}

export const store = {
  subscribe(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  getState: () => state,
  getDataVersion: () => dataVersion,
  // What the charts should draw right now: a session's recording, or the live window.
  getChartSource() {
    const session = state.sessions.find((s) => s.id === state.viewId)
    if (session) return { buffer: recordings.get(session.id), session }
    return { buffer: live, session: null }
  },
  onFrame(listener) {
    frameListeners.add(listener)
    return () => frameListeners.delete(listener)
  },
}

/* ---------- samples ---------- */

function push(buffer, t, message) {
  buffer.t.push(t)
  for (const field of SAMPLE_FIELDS) buffer[field].push(toNumber(message[field]))
}

function ingestSample(message) {
  const t = message.t
  const lastT = live.t[live.t.length - 1]
  if (!Number.isFinite(t) || (lastT !== undefined && t <= lastT)) return // charts need rising time

  push(live, t, message)
  // Trim in batches rather than on every sample.
  if (live.t[0] < t - LIVE_WINDOW_S - 5) {
    const cut = live.t.findIndex((value) => value >= t - LIVE_WINDOW_S - 1)
    for (const key of Object.keys(live)) live[key].splice(0, cut)
  }

  if (state.activeId !== null) push(recordings.get(state.activeId), t, message)

  dataVersion++
  samplesThisSecond++
  lastSampleAt = Date.now()
  if (lastSampleAt - lastTileRefresh >= TILE_REFRESH_MS) {
    lastTileRefresh = lastSampleAt
    setState({ latest: message })
  }
}

/* ---------- sessions ---------- */

function showError(message) {
  clearTimeout(errorTimer)
  setState({ error: message })
  errorTimer = setTimeout(() => setState({ error: null }), ERROR_SHOW_MS)
}

// The voice baseline that follows the silent one: the subject reads sentences aloud.
// null when it hasn't started, or the backend skipped it (no microphone).
function toVoice(remote) {
  if (!remote || !Number.isFinite(remote.start) || !Number.isFinite(remote.end)) return null
  return {
    startT: remote.start,
    endT: remote.end,
    sentences: Array.isArray(remote.sentences) ? remote.sentences.map(String) : [],
    done: Boolean(remote.done),
    reading: remote.reading ?? null, // the reading's speech features, compared with each answer
  }
}

// A question as the dashboard keeps it. endT: when "Done asking" was clicked (null if not);
// responded: its "response" arrived; voice: that response's per-answer voice features.
function toQuestion(remote, fallbackLabel) {
  return {
    label: String(remote.label ?? fallbackLabel),
    t: remote.t,
    endT: Number.isFinite(remote.end) ? remote.end : null,
    spike: toNumber(remote.spike),
    signals: remote.signals ?? {},
    voice: remote.voice ?? null,
    responded: Boolean(remote.done),
  }
}

function beginSession({ name, startT, baselineEndT, baseline = null, voice = null, questions = [] }) {
  if (state.activeId !== null) endSession()
  clearTimeout(startTimer)
  const id = nextSessionId++
  recordings.set(id, newBuffer())
  const session = {
    id,
    name,
    startT,
    baselineEndT,
    baseline,
    voice,
    questions,
    summary: null,
    endT: null,
    peakStress: null,
    secondsOverAlert: null,
  }
  pendingName = null
  setState({ sessions: [...state.sessions, session], activeId: id, viewId: id, starting: false })
}

function endSession() {
  const session = activeSession()
  if (!session) return
  const recording = recordings.get(session.id)
  const endT = recording.t[recording.t.length - 1] ?? session.startT

  let peakStress = null
  let secondsOverAlert = 0
  for (let i = 0; i < recording.t.length; i++) {
    const stress = recording.stress[i]
    if (stress === null) continue
    peakStress = Math.max(peakStress ?? 0, stress)
    if (session.baseline && stress > session.baseline.stressAlert && i > 0) {
      secondsOverAlert += recording.t[i] - recording.t[i - 1]
    }
  }

  setState({
    activeId: null,
    sessions: state.sessions.map((s) =>
      s.id === session.id ? { ...s, endT, peakStress, secondsOverAlert: Math.round(secondsOverAlert) } : s,
    ),
  })
}

function onBaseline(message) {
  if (message.phase === 'start') {
    const active = activeSession()
    // The status message may already have told us about this session.
    if (active && Math.abs(active.startT - message.t) < 0.5) return
    const duration = Number.isFinite(message.duration) ? message.duration : 60
    beginSession({ name: pendingName ?? 'Session', startT: message.t, baselineEndT: message.t + duration })
  } else if (message.phase === 'done') {
    const active = activeSession()
    if (active) updateSession(active.id, { baseline: toBaseline(message), baselineEndT: message.t })
  }
}

function onVoiceBaseline(message) {
  const active = activeSession()
  if (!active || !Number.isFinite(message.t)) return
  if (message.phase === 'start') {
    const duration = Number.isFinite(message.duration) ? message.duration : 25
    const voice = toVoice({ start: message.t, end: message.t + duration, sentences: message.sentences, done: false })
    updateSession(active.id, { voice })
  } else if (message.phase === 'done' && active.voice) {
    updateSession(active.id, {
      voice: { ...active.voice, endT: message.t, done: true, reading: message.reading ?? null },
      baseline: toBaseline(message) ?? active.baseline,
    })
  }
}

function onQuestion(message) {
  const active = activeSession()
  if (!active || !Number.isFinite(message.t)) return
  if (message.phase === 'end') {
    const index = active.questions.findLastIndex((q) => q.label === message.label)
    if (index === -1) return
    const questions = active.questions.map((q, i) => (i === index ? { ...q, endT: message.t } : q))
    return updateSession(active.id, { questions })
  }
  const question = toQuestion({ ...message, done: false }, `Q${active.questions.length + 1}`)
  updateSession(active.id, { questions: [...active.questions, question] })
}

function onResponse(message) {
  const active = activeSession() ?? state.sessions[state.sessions.length - 1]
  if (!active) return
  const index = active.questions.findLastIndex((q) => q.label === message.label)
  if (index === -1) return
  const questions = active.questions.map((q, i) =>
    i === index
      ? { ...q, spike: toNumber(message.spike), signals: message.signals ?? {}, voice: message.voice ?? null, responded: true }
      : q,
  )
  updateSession(active.id, { questions })
}

function onSummary(message) {
  const target = activeSession() ?? [...state.sessions].reverse().find((s) => !s.summary)
  if (!target) return
  const summary = Array.isArray(message.questions) ? message.questions : []
  if (state.activeId === target.id) endSession()
  updateSession(target.id, { summary })
}

function onStatus(message) {
  if (message.components && typeof message.components === 'object') setState({ components: message.components })
  if (!('session' in message)) return

  const remote = message.session
  const active = activeSession()
  if (remote && !active && Number.isFinite(remote.started)) {
    // A session we didn't see start: the page reloaded, or another tab started it.
    beginSession({
      name: pendingName ?? String(remote.subject ?? 'Session'),
      startT: remote.started,
      baselineEndT: remote.baseline_end,
      baseline: toBaseline(remote.baseline),
      voice: toVoice(remote.voice_baseline),
      questions: Array.isArray(remote.questions) ? remote.questions.map((q, i) => toQuestion(q, `Q${i + 1}`)) : [],
    })
  } else if (!remote && active && !state.starting) {
    endSession() // the backend no longer has it (stopped elsewhere, or restarted)
  }
}

function handleSocketMessage(message) {
  switch (message.type) {
    case 'sample':
      return ingestSample(message)
    case 'frame':
      if (typeof message.jpeg === 'string') frameListeners.forEach((listener) => listener(message))
      return
    case 'status':
      return onStatus(message)
    case 'baseline':
      return onBaseline(message)
    case 'voice_baseline':
      return onVoiceBaseline(message)
    case 'question':
      return onQuestion(message)
    case 'response':
      return onResponse(message)
    case 'summary':
      return onSummary(message)
    case 'error':
      if (state.starting) setState({ starting: false })
      return showError(String(message.message ?? 'The backend reported an error.'))
    default:
    // Unknown types are ignored, per the contract.
  }
}

/* ---------- operator controls ---------- */

export function startSession(name) {
  if (!socket.send({ type: 'start', subject: name })) {
    showError('The backend is offline, so the session could not start.')
    return
  }
  pendingName = name
  setState({ starting: true })
  clearTimeout(startTimer)
  startTimer = setTimeout(() => {
    if (!state.starting) return
    setState({ starting: false })
    showError("The backend didn't start the session. Check that it's running and try again.")
  }, START_TIMEOUT_MS)
}

// The interrogator is asking a question right now. The backend timestamps it and
// answers with a "question" echo, then a "response" about 10 s later.
export function askQuestion(text = '') {
  const message = { type: 'question' }
  if (text.trim()) message.text = text.trim()
  if (!socket.send(message)) showError('The backend is offline, so the question was not recorded.')
}

// The interrogator finished asking: response latency is measured from here.
export function endQuestion() {
  if (!socket.send({ type: 'question_end' })) showError('The backend is offline, so the end of the question was not recorded.')
}

export function stopSession() {
  // Without a connection the backend can't be told, but the operator still needs out.
  if (!socket.send({ type: 'stop' })) endSession()
}

export function viewSession(id) {
  setState({ viewId: id })
}

export function viewLive() {
  setState({ viewId: null })
}

export function dismissError() {
  clearTimeout(errorTimer)
  setState({ error: null })
}

/* ---------- connection ---------- */

export function connect() {
  socket = (IS_DEMO ? connectDemo : connectSocket)({
    url: WS_URL,
    onMessage: handleSocketMessage,
    onStatus: (connection) => setState(connection === 'open' ? { connection } : { connection, components: null }),
  })

  setInterval(() => {
    setState({
      sampleHz: samplesThisSecond,
      // Clear the tiles rather than show old numbers as if they were current.
      latest: Date.now() - lastSampleAt > STALE_AFTER_MS ? null : state.latest,
    })
    samplesThisSecond = 0
  }, 1000)
}
