import { useEffect, useRef, useState } from 'react'
import { IS_DEMO } from '../lib/store'
import logoUrl from '../assets/verifi-logo.png'

const COMPONENTS = [
  { key: 'arduino', label: 'Arduino' },
  { key: 'gsr', label: 'GSR' },
  { key: 'pulse', label: 'Pulse' },
  { key: 'breathing', label: 'Breathing' },
  { key: 'camera', label: 'Camera' },
  { key: 'face', label: 'Face tracking' },
  { key: 'voice', label: 'Voice' },
]

const STATES = {
  ok: { tone: 'good', text: 'Connected' },
  simulated: { tone: 'warning', text: 'Simulated' },
  missing: { tone: 'critical', text: 'Not found' },
  off: { tone: 'muted', text: 'Off' },
  unknown: { tone: 'muted', text: 'Unknown' },
}

const BACKEND_STATES = {
  open: STATES.ok,
  connecting: { tone: 'warning', text: 'Connecting' },
  closed: { tone: 'critical', text: 'Offline' },
}

// One line for the whole rig: the worst thing first.
function summarize(connection, components) {
  if (!IS_DEMO && connection === 'closed') return { tone: 'critical', text: 'Backend offline' }
  if (!IS_DEMO && connection !== 'open') return { tone: 'warning', text: 'Connecting to the backend…' }
  if (!components) return { tone: 'muted', text: 'Checking sensors…' }
  const statuses = COMPONENTS.map((c) => ({ ...c, status: components[c.key] ?? 'unknown' }))
  const missing = statuses.filter((s) => s.status === 'missing')
  if (missing.length) {
    return { tone: 'critical', text: missing.length === 1 ? `${missing[0].label} not found` : `${missing.length} sensors not found` }
  }
  if (IS_DEMO) return { tone: 'warning', text: 'Simulated in browser' }
  if (statuses.some((s) => s.status === 'simulated')) return { tone: 'warning', text: 'Running on simulated data' }
  const off = statuses.filter((s) => s.status === 'off').length
  return { tone: 'good', text: off ? `All sensors OK · ${off} off` : 'All sensors OK' }
}

function Row({ label, state, detail }) {
  return (
    <li className={`status-row chip-${state.tone}`}>
      <span className="chip-dot" aria-hidden="true" />
      <span className="chip-label">{label}</span>
      <span className="chip-state">
        {state.text}
        {detail && ` · ${detail}`}
      </span>
    </li>
  )
}

export default function Navbar({ connection, components, sampleHz }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const summary = summarize(connection, components)
  const rate = connection === 'open' ? `${sampleHz}/s` : null

  // Closes on Escape or a click anywhere else.
  useEffect(() => {
    if (!open) return undefined
    const onPointer = (event) => {
      if (!ref.current?.contains(event.target)) setOpen(false)
    }
    const onKey = (event) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <header className="navbar">
      <div className="brand">
        {/* The logo's shape, filled with the text colour so it suits light and dark mode. */}
        <span className="brand-logo" aria-hidden="true" style={{ '--logo': `url(${logoUrl})` }} />
        <h1>VeriFi</h1>
        <span>Operator</span>
      </div>
      <div className="navbar-status" ref={ref}>
        <button
          type="button"
          className={`chip chip-${summary.tone} status-summary`}
          aria-expanded={open}
          aria-controls="component-status"
          onClick={() => setOpen((value) => !value)}
        >
          <span className="chip-dot" aria-hidden="true" />
          <span className="chip-label">{summary.text}</span>
          {rate && <span className="chip-state">{rate}</span>}
          <span className="status-caret" aria-hidden="true">
            ▾
          </span>
        </button>
        {open && (
          <div className="status-popover" id="component-status">
            <p className="eyebrow">Components</p>
            <ul aria-label="Component status">
              <Row
                label="Backend"
                state={IS_DEMO ? { tone: 'warning', text: 'Simulated in browser' } : BACKEND_STATES[connection]}
                detail={rate}
              />
              {COMPONENTS.map(({ key, label }) => (
                <Row key={key} label={label} state={STATES[components?.[key]] ?? STATES.unknown} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </header>
  )
}
