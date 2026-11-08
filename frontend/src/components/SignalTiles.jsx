import { useState } from 'react'
import { GROUPS, SIGNALS, alertState, thresholdsFor } from '../lib/signals'
import { readSectionOpen, writeSectionOpen } from '../lib/useSectionOpen'
import Icon from './Icon'
import VoiceAnswers from './VoiceAnswers'

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

function Status({ signal, value, thresholds, baseline, componentStatus, speaking }) {
  if (!Number.isFinite(value)) {
    if (componentStatus === 'missing') return <span className="tile-note">Sensor not found</span>
    if (componentStatus === 'off') return <span className="tile-note">Off</span>
    // A working mic gives no pitch during silence; that's normal, not a fault.
    if (signal.source === 'voice' && componentStatus === 'ok') return <span className="tile-note">Not speaking</span>
    return <span className="tile-note">No data yet</span>
  }
  if (signal.speechOnly && !speaking) return <span className="tile-note">Not speaking</span>
  if (!thresholds) return <span className="tile-note">{baseline ? 'Not in baseline' : 'No baseline yet'}</span>

  const state = alertState(value, thresholds)
  if (state) {
    return (
      <span className="tile-alert">
        <span aria-hidden="true">{state === 'above' ? '▲' : '▼'}</span> {state === 'above' ? 'Above' : 'Below'} normal
      </span>
    )
  }
  const sd = (value - thresholds.mean) / thresholds.std
  return (
    <span className="tile-note">
      {sd >= 0 ? '+' : '−'}
      {Math.abs(sd).toFixed(1)} SD from baseline
    </span>
  )
}

// Thresholds a tile compares its value with: none for a speech-only signal while the subject is quiet.
function tileThresholds(signal, latest, baseline) {
  if (signal.speechOnly && !Number.isFinite(latest?.pitch)) return null
  return thresholdsFor({ ...signal, field: signal.tileField ?? signal.field }, baseline)
}

function Tile({ signal, latest, baseline, components, open, onToggle }) {
  const value = rawValue(latest, signal.tileField ?? signal.field)
  const thresholds = tileThresholds(signal, latest, baseline)
  const alerting = alertState(value, thresholds) !== null
  return (
    <li>
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
        <Status
          signal={signal}
          value={value}
          thresholds={thresholds}
          baseline={baseline}
          componentStatus={components?.[signal.source]}
          speaking={Number.isFinite(latest?.pitch)}
        />
      </button>
    </li>
  )
}

// Signals in three dropdowns (body sensors, vision, voice). Each summary line says
// how many of its signals are outside normal, so a closed dropdown still flags a reaction.
export default function SignalTiles({ latest, baseline, components, openKeys, onToggle, session }) {
  // Closed by default (the summary line still flags a reaction); remembered like the other dropdowns.
  const [openGroups, setOpenGroups] = useState(() =>
    Object.fromEntries(GROUPS.map((g) => [g.key, readSectionOpen(`signals-${g.key}`)])),
  )

  return (
    <div className="signal-groups">
      {GROUPS.map((group) => {
        const signals = SIGNALS.filter((s) => s.group === group.key)
        const alerts = signals.filter(
          (s) => alertState(rawValue(latest, s.tileField ?? s.field), tileThresholds(s, latest, baseline)) !== null,
        ).length
        return (
          <details
            key={group.key}
            className="signal-group"
            open={openGroups[group.key]}
            onToggle={(event) => {
              const open = event.currentTarget.open
              writeSectionOpen(`signals-${group.key}`, open)
              setOpenGroups((groups) => (groups[group.key] === open ? groups : { ...groups, [group.key]: open }))
            }}
          >
            <summary>
              <Icon name={group.key} />
              <span className="signal-group-title">{group.label}</span>
              {alerts > 0 ? (
                <span className="tile-alert">
                  <span aria-hidden="true">▲</span> {alerts} outside normal
                </span>
              ) : (
                <span className="tile-note">
                  {signals.length} signals{group.key === 'voice' && ' · answers'}
                </span>
              )}
            </summary>
            <ul className="tiles" aria-label={`${group.label}. Select one to show its chart.`}>
              {signals.map((signal) => (
                <Tile
                  key={signal.key}
                  signal={signal}
                  latest={latest}
                  baseline={baseline}
                  components={components}
                  open={openKeys.includes(signal.key)}
                  onToggle={onToggle}
                />
              ))}
            </ul>
            {group.key === 'voice' && <VoiceAnswers session={session} />}
          </details>
        )
      })}
    </div>
  )
}
