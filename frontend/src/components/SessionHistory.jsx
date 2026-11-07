import { viewLive, viewSession } from '../lib/store'
import { formatDuration } from '../lib/format'

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

export default function SessionHistory({ sessions, viewId, activeId }) {
  if (sessions.length === 0) return null
  return (
    <section className="card history" aria-label="Sessions">
      <div className="card-head">
        <h2 className="section-title">Sessions</h2>
        {viewId !== null && activeId === null && (
          <button className="button-link" type="button" onClick={viewLive}>
            Back to live
          </button>
        )}
      </div>
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
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
