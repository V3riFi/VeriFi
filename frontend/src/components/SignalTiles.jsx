import { SIGNALS, alertState, thresholdsFor } from '../lib/signals'

function formatValue(signal, value) {
  if (!Number.isFinite(value)) return '–'
  if (signal.boolean) return value ? 'Yes' : 'No'
  if (Math.abs(value) < 10) return value.toFixed(2).replace(/\.?0+$/, '') || '0'
  return Math.round(value)
}

function rawValue(latest, field) {
  const value = latest?.[field]
  return typeof value === 'boolean' ? Number(value) : value
}

function Status({ signal, value, baseline, componentStatus }) {
  if (!Number.isFinite(value)) {
    if (componentStatus === 'missing') return <span className="tile-note">Sensor not found</span>
    // A working mic gives no pitch during silence; that's normal, not a fault.
    if (signal.source === 'voice' && componentStatus === 'ok') return <span className="tile-note">Not speaking</span>
    return <span className="tile-note">No data yet</span>
  }
  const field = signal.tileField ?? signal.field
  const thresholds = thresholdsFor({ ...signal, field }, baseline)
  if (!thresholds) return <span className="tile-note">{baseline ? 'Not in baseline' : 'No baseline yet'}</span>

  const state = alertState(value, thresholds)
  if (state) {
    return (
      <span className="tile-alert">
        <span aria-hidden="true">{state === 'above' ? '▲' : '▼'}</span> {state === 'above' ? 'Above' : 'Below'} normal
      </span>
    )
  }
  const sd = (value - thresholds.mean) / baseline.std[field]
  return (
    <span className="tile-note">
      {sd >= 0 ? '+' : '−'}
      {Math.abs(sd).toFixed(1)} SD from baseline
    </span>
  )
}

export default function SignalTiles({ latest, baseline, components, openKeys, onToggle }) {
  return (
    <ul className="tiles" aria-label="Signals. Select one to show its chart.">
      {SIGNALS.map((signal) => {
        const value = rawValue(latest, signal.tileField ?? signal.field)
        const open = openKeys.includes(signal.key)
        const thresholds = thresholdsFor({ ...signal, field: signal.tileField ?? signal.field }, baseline)
        const alerting = alertState(value, thresholds) !== null
        return (
          <li key={signal.key}>
            <button
              type="button"
              className={`tile${alerting ? ' tile-alerting' : ''}`}
              aria-pressed={open}
              onClick={() => onToggle(signal.key)}
            >
              <span className="tile-label">
                {signal.label}
                <span className="tile-toggle" aria-hidden="true">
                  {open ? 'Hide' : 'Show'}
                </span>
              </span>
              <span className="tile-value">
                {formatValue(signal, value)} <small>{signal.tileUnit ?? signal.unit}</small>
              </span>
              <Status signal={signal} value={value} baseline={baseline} componentStatus={components?.[signal.source]} />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
