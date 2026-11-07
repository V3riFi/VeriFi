// Stands in for the backend's session logic until it exists. Emits the same
// `trial` and `result` messages the README contract defines, so the UI runs the
// same code path it will use with the real backend.
//
// The scores are random. They say nothing about the signals on screen.

const ITEMS = ['knife', 'rope', 'gun', 'bat', 'poison']
const FILLER = 'hammer' // shown first each round, never scored (see backend/scoring.py)
const ROUNDS = 2
const LEAD_IN_MS = 2000
const TRIAL_MS = 4000 // the real window is about 10s; shorter here to keep UI testing quick
const GAP_MS = 1500

function shuffled(list) {
  const copy = [...list]
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[copy[i], copy[j]] = [copy[j], copy[i]]
  }
  return copy
}

function randomResult() {
  const standout = ITEMS[Math.floor(Math.random() * ITEMS.length)]
  const raw = ITEMS.map((item) => Math.random() * 1.2 + (item === standout ? 1.5 : 0))
  const mean = raw.reduce((sum, value) => sum + value, 0) / raw.length
  const scores = Object.fromEntries(ITEMS.map((item, i) => [item, Math.round((raw[i] - mean) * 100) / 100]))
  return { type: 'result', scores, pick: standout }
}

// Starts a simulated session and returns a function that cancels it.
export function runMockSession(emit) {
  const steps = []
  for (let round = 1; round <= ROUNDS; round++) {
    for (const item of [FILLER, ...shuffled(ITEMS)]) {
      steps.push({ round, item, phase: 'start', waitMs: GAP_MS })
      steps.push({ round, item, phase: 'end', waitMs: TRIAL_MS })
    }
  }

  let timer = null
  let index = 0

  function next() {
    const step = steps[index++]
    if (!step) {
      timer = setTimeout(() => emit(randomResult()), GAP_MS)
      return
    }
    timer = setTimeout(() => {
      emit({ type: 'trial', t: Date.now() / 1000, round: step.round, item: step.item, phase: step.phase })
      next()
    }, step.waitMs)
  }

  // The first start waits for the lead-in instead of the usual gap.
  steps[0].waitMs = LEAD_IN_MS
  next()

  return () => clearTimeout(timer)
}
