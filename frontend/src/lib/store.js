import { connectSocket } from './socket'
import { runMockSession } from './mockSession'

const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws'
export const DEFAULT_SOURCE = import.meta.env.VITE_SESSION_SOURCE === 'backend' ? 'backend' : 'mock'

export const LIVE_WINDOW_S = 60
const PRE_ROLL_S = 5 // signal kept from before a session, so its first response has context
const STALE_AFTER_MS = 2000
const TILE_REFRESH_MS = 250
const BACKEND_START_TIMEOUT_MS = 5000
const FIELDS = ['gsr', 'hr', 'flex']

const emptyBuffer = () => ({ t: [], gsr: [], hr: [], flex: [] })
const idleSession = { id: 0, status: 'idle', source: null, subject: '', truth: '', trials: [], result: null, startedAt: null, warning: null }

const finiteOrNull = (value) => (Number.isFinite(value) ? value : null)

// Samples arrive 20 times a second, so they go into plain arrays that charts read
// each frame. React state (`state`) only changes for things worth re-rendering.
let live = emptyBuffer()
let record = null
let recording = false
let dataVersion = 0

let state = {
  connection: 'connecting',
  latest: null, // most recent sample, refreshed a few times a second
  sampleHz: 0,
  session: idleSession,
}

const listeners = new Set()
let socket = null
let cancelMock = null
let startTimer = null
let nextSessionId = 1
let lastTileRefresh = 0
let lastSampleAt = 0
let samplesThisSecond = 0

function setState(patch) {
  state = { ...state, ...patch }
  dataVersion++
  listeners.forEach((listener) => listener())
}

function setSession(patch) {
  setState({ session: { ...state.session, ...patch } })
}

export const store = {
  subscribe(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  getState: () => state,
  getLive: () => live,
  getRecord: () => record,
  getDataVersion: () => dataVersion,
}

/* ---------- samples ---------- */

function ingestSample(message) {
  const t = message.t
  const lastT = live.t[live.t.length - 1]
  if (!Number.isFinite(t) || (lastT !== undefined && t <= lastT)) return // charts need rising time

  live.t.push(t)
  for (const field of FIELDS) live[field].push(finiteOrNull(message[field]))

  // Trim in batches rather than on every sample.
  if (live.t[0] < t - LIVE_WINDOW_S - 5) {
    const cut = live.t.findIndex((value) => value >= t - LIVE_WINDOW_S - 1)
    live.t.splice(0, cut)
    for (const field of FIELDS) live[field].splice(0, cut)
  }

  if (recording) {
    record.t.push(t)
    for (const field of FIELDS) record[field].push(finiteOrNull(message[field]))
  }

  dataVersion++
  samplesThisSecond++
  lastSampleAt = Date.now()
  if (lastSampleAt - lastTileRefresh >= TILE_REFRESH_MS) {
    lastTileRefresh = lastSampleAt
    setState({ latest: message })
  }
}

function startRecording() {
  const from = live.t.findIndex((t) => t >= live.t[live.t.length - 1] - PRE_ROLL_S)
  const start = Math.max(from, 0)
  record = {
    t: live.t.slice(start),
    gsr: live.gsr.slice(start),
    hr: live.hr.slice(start),
    flex: live.flex.slice(start),
  }
  recording = true
}

/* ---------- sessions ---------- */

function clearSessionTimers() {
  clearTimeout(startTimer)
  cancelMock?.()
  cancelMock = null
}

function beginSession({ status, source, subject = '', truth = '' }) {
  clearSessionTimers()
  startRecording()
  setState({
    session: { ...idleSession, id: nextSessionId++, status, source, subject, truth, startedAt: Date.now() },
  })
}

function applyTrial(message) {
  const { t, round, item, phase } = message
  if (!Number.isFinite(t) || typeof item !== 'string') return

  if (phase === 'start') {
    const { status } = state.session
    // A session this tab didn't start, or one we rejoined after a reconnect.
    if (status === 'idle' || status === 'done') beginSession({ status: 'running', source: 'backend' })
    clearTimeout(startTimer)
    const trials = state.session.trials.map((trial) => (trial.end === null ? { ...trial, end: t } : trial))
    setSession({ status: 'running', warning: null, trials: [...trials, { round, item, start: t, end: null }] })
  } else if (phase === 'end') {
    const trials = state.session.trials
    const last = trials[trials.length - 1]
    if (!last || last.end !== null || last.item !== item) return
    setSession({ trials: [...trials.slice(0, -1), { ...last, end: t }] })
  }
}

function applyResult(message) {
  const entries = Object.entries(message.scores ?? {}).filter(([, score]) => Number.isFinite(score))
  if (entries.length === 0) return
  if (state.session.status === 'idle' || state.session.status === 'done') {
    beginSession({ status: 'running', source: 'backend' })
  }
  clearSessionTimers()
  recording = false
  const scores = entries.map(([item, score]) => ({ item, score })).sort((a, b) => b.score - a.score)
  const pick = typeof message.pick === 'string' ? message.pick : scores[0].item
  setSession({ status: 'done', warning: null, result: { scores, pick } })
}

function applySessionMessage(message) {
  if (message.type === 'trial') applyTrial(message)
  else if (message.type === 'result') applyResult(message)
}

function isMockRunning() {
  const { source, status } = state.session
  return source === 'mock' && (status === 'waiting' || status === 'running')
}

function handleSocketMessage(message) {
  if (message.type === 'sample') {
    ingestSample(message)
  } else if (!isMockRunning()) {
    applySessionMessage(message)
  }
  // Unknown types fall through applySessionMessage untouched, per the contract.
}

export function startSession({ subject, truth, source }) {
  if (source === 'mock') {
    beginSession({ status: 'waiting', source, subject, truth })
    cancelMock = runMockSession(applySessionMessage)
    return
  }
  // `truth` is only logged by the backend for measuring accuracy.
  if (!socket.send({ type: 'start', subject, truth })) return
  beginSession({ status: 'waiting', source, subject, truth })
  startTimer = setTimeout(() => {
    setSession({
      warning: 'No trial from the backend after 5 seconds. Check that it handles "start" messages, or go back and run a simulated session.',
    })
  }, BACKEND_START_TIMEOUT_MS)
}

export function resetSession() {
  clearSessionTimers()
  recording = false
  record = null
  setState({ session: idleSession })
}

/* ---------- connection ---------- */

export function connect() {
  socket = connectSocket({
    url: WS_URL,
    onMessage: handleSocketMessage,
    onStatus: (connection) => setState({ connection }),
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
