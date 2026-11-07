import { useEffect, useState } from 'react'
import { resetSession } from '../lib/store'

function useElapsedSeconds(startedAt) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return Math.max(0, Math.floor((now - startedAt) / 1000))
}

export default function SessionProgress({ session, connection, onShowSubject }) {
  const elapsed = useElapsedSeconds(session.startedAt)
  const { trials, source, status } = session
  const last = trials[trials.length - 1]
  const showing = last && last.end === null ? last : null
  const isMock = source === 'mock'

  // The contract has no stop message, so a running backend session can't be
  // cancelled from here. Leaving is only offered when it isn't really running.
  const canLeave = isMock || status === 'waiting' || connection !== 'open'

  return (
    <div className="panel">
      <h2 className="section-title">Session in progress</h2>
      {isMock && <SimulatedBadge />}

      <p className="progress-item">{showing ? showing.item : status === 'waiting' ? 'Starting…' : 'Between items'}</p>
      <dl className="facts">
        {session.subject && (
          <div>
            <dt>Subject</dt>
            <dd>{session.subject}</dd>
          </div>
        )}
        {last && (
          <div>
            <dt>Round</dt>
            <dd>{last.round}</dd>
          </div>
        )}
        <div>
          <dt>Items shown</dt>
          <dd>{trials.length}</dd>
        </div>
        <div>
          <dt>Elapsed</dt>
          <dd>
            {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}
          </dd>
        </div>
      </dl>

      {session.warning && <Notice>{session.warning}</Notice>}
      {!isMock && connection !== 'open' && (
        <Notice>Connection to the backend was lost. Results from this session may not arrive.</Notice>
      )}

      <button className="button-primary" type="button" onClick={onShowSubject}>
        Show subject screen
      </button>
      {canLeave && (
        <button className="button-quiet" type="button" onClick={resetSession}>
          {isMock ? 'Cancel session' : 'Back to setup'}
        </button>
      )}
    </div>
  )
}

export function SimulatedBadge() {
  return (
    <p className="notice notice-warning">
      <strong>Simulated</strong> Not a measurement. Generated in this browser.
    </p>
  )
}

function Notice({ children }) {
  return <p className="notice notice-warning">{children}</p>
}
