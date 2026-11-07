const COMPONENTS = [
  { key: 'arduino', label: 'Arduino' },
  { key: 'gsr', label: 'GSR' },
  { key: 'pulse', label: 'Pulse' },
  { key: 'breathing', label: 'Breathing belt' },
  { key: 'camera', label: 'Camera' },
  { key: 'face', label: 'Face tracking' },
  { key: 'voice', label: 'Voice' },
]

import { IS_DEMO } from '../lib/store'

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

function Chip({ label, state, detail }) {
  return (
    <li className={`chip chip-${state.tone}`}>
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
  return (
    <header className="navbar">
      <div className="brand">
        <h1>VeriFi</h1>
        <span>Operator</span>
      </div>
      <ul className="chips" aria-label="Component status">
        <Chip
          label="Backend"
          state={IS_DEMO ? { tone: 'warning', text: 'Simulated in browser' } : BACKEND_STATES[connection]}
          detail={connection === 'open' ? `${sampleHz}/s` : null}
        />
        {COMPONENTS.map(({ key, label }) => (
          <Chip key={key} label={label} state={STATES[components?.[key]] ?? STATES.unknown} />
        ))}
      </ul>
    </header>
  )
}
