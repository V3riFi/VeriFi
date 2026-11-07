import { useEffect, useState, useSyncExternalStore } from 'react'
import { store } from './lib/store'
import Header from './components/Header'
import StatTiles from './components/StatTiles'
import SignalCharts from './components/SignalCharts'
import SessionSetup from './components/SessionSetup'
import SessionProgress from './components/SessionProgress'
import SubjectScreen from './components/SubjectScreen'
import Results from './components/Results'

export default function App() {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const { session, connection } = state
  const [dismissedSessionId, setDismissedSessionId] = useState(null)

  const active = session.status === 'waiting' || session.status === 'running'
  // Subjects see only the item: watching their own signals would affect the test.
  const showSubject = active && dismissedSessionId !== session.id

  useEffect(() => {
    if (!showSubject) return
    const onKey = (event) => {
      if (event.key === 'Escape') setDismissedSessionId(session.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showSubject, session.id])

  if (showSubject) return <SubjectScreen session={session} />

  const done = session.status === 'done'

  return (
    <div className="app">
      <Header connection={connection} sampleHz={state.sampleHz} />
      <StatTiles latest={state.latest} />
      <main className="layout">
        <section className="charts" aria-label="Signals">
          <h2 className="section-title">{done ? 'Session recording' : 'Live signals, last 60 seconds'}</h2>
          <SignalCharts key={done ? `review-${session.id}` : 'live'} review={done} session={session} />
        </section>
        <aside className="side">
          {session.status === 'idle' && <SessionSetup connection={connection} />}
          {active && (
            <SessionProgress
              session={session}
              connection={connection}
              onShowSubject={() => setDismissedSessionId(null)}
            />
          )}
          {done && <Results session={session} />}
        </aside>
      </main>
    </div>
  )
}
