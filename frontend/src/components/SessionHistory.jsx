import { viewLive, viewSession } from '../lib/store'
import { formatDuration } from '../lib/format'
import { BANDS, INSUFFICIENT, bandCounts } from '../lib/verdict'

// "2 high · 1 low": how the session's answers read, most concerning first.
function Counts({ questions }) {
  const counts = bandCounts(questions)
  const parts = [...BANDS, INSUFFICIENT].filter((b) => counts[b.key] > 0)
  if (!parts.length) return null
  return (
    <span className="history-counts">
      {parts.map((b) => (
        <span key={b.key} className={`history-count band-${b.key}`}>
          <span className="band-glyph" aria-hidden="true">
            {b.glyph}
          </span>
          {counts[b.key]} {b.key === INSUFFICIENT.key ? 'no signal' : b.label.toLowerCase()}
        </span>
      ))}
    </span>
  )
}

function clockTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function Details({ session }) {
  if (session.endT === null) return <span>Running</span>
  const parts = [formatDuration(session.endT - session.startT)]
  if (session.peakStress !== null) parts.push(`peak stress ${Math.round(session.peakStress)}`)
  if (session.secondsOverAlert) parts.push(`${session.secondsOverAlert} s over alert`)
  const top = session.summary?.find((q) => Number.isFinite(q.spike))
  if (top) parts.push(`top ${top.label} ${top.spike.toFixed(1)} SD`)
  return <span>{parts.join(' · ')}</span>
}

// The list only: the "Sessions" dropdown around it (App.jsx) carries the title.
export default function SessionHistory({ sessions, viewId, activeId }) {
  if (sessions.length === 0) return null
  return (
    <div className="history">
      {viewId !== null && activeId === null && (
        <button className="button-link history-back" type="button" onClick={viewLive}>
          ← Back to live
        </button>
      )}
      <ul>
        {[...sessions].reverse().map((session) => (
          <li key={session.id}>
            <button
              type="button"
              className="history-row"
              aria-current={session.id === viewId ? 'true' : undefined}
              onClick={() => viewSession(session.id)}
              disabled={activeId !== null && session.id !== activeId}
            >
              <span className="history-name">
                {session.name} <small>{clockTime(session.startT)}</small>
              </span>
              <Details session={session} />
              <Counts questions={session.questions} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
