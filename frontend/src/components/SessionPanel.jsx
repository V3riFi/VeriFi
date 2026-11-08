import { useEffect, useState } from 'react'
import { IS_DEMO, askQuestion, dismissError, endQuestion, startSession, stopSession } from '../lib/store'
import { formatDuration } from '../lib/format'

function useNow() {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 500)
    return () => clearInterval(timer)
  }, [])
  return now
}

function Countdown({ label, startT, endT, now }) {
  const left = endT - now
  const duration = endT - startT
  const progress = duration > 0 ? Math.min(1, Math.max(0, 1 - left / duration)) : 1
  return (
    <>
      <div
        className="progress"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
      >
        <span style={{ width: `${progress * 100}%` }} />
      </div>
      <small>{left > 0 ? `${Math.ceil(left)} s left` : 'Finishing…'}</small>
    </>
  )
}

function ActiveSession({ session }) {
  const now = useNow()
  const recordingVoice = session.baseline && session.voice && !session.voice.done

  return (
    <>
      <div className="session-live">
        <span className="session-name">{session.name}</span>
        <span className="session-clock">{formatDuration(now - session.startT)}</span>
      </div>
      {!session.baseline ? (
        <div className="baseline-progress">
          <p>
            <strong>Recording baseline.</strong> Keep the subject still and quiet, and don't ask anything yet.
          </p>
          <Countdown label="Baseline recording" startT={session.startT} endT={session.baselineEndT} now={now} />
        </div>
      ) : recordingVoice ? (
        <div className="baseline-progress">
          <p>
            <strong>Recording voice baseline.</strong> Have the subject read these aloud at a normal pace, then repeat
            them until the bar fills.
          </p>
          <ol className="voice-sentences">
            {session.voice.sentences.map((sentence) => (
              <li key={sentence}>{sentence}</li>
            ))}
          </ol>
          <Countdown label="Voice baseline recording" startT={session.voice.startT} endT={session.voice.endT} now={now} />
        </div>
      ) : (
        <AskQuestion questions={session.questions} />
      )}
      <button className="button-stop" type="button" onClick={stopSession}>
        Stop session
      </button>
    </>
  )
}

// Press Ask (or Space anywhere outside a text field) the moment you start asking,
// so the response window lines up with the question. Then press Done asking (or
// Space again) the moment you finish: response latency is measured from there.
function AskQuestion({ questions }) {
  const [text, setText] = useState('')
  const last = questions[questions.length - 1]
  const asking = Boolean(last && last.endT === null && !last.responded)

  const ask = () => {
    askQuestion(text)
    setText('')
  }

  useEffect(() => {
    const onKey = (event) => {
      if (event.code !== 'Space' || event.repeat) return
      const tag = event.target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON' || event.target?.isContentEditable) return
      event.preventDefault()
      if (asking) endQuestion()
      else askQuestion()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [asking])

  if (asking) {
    return (
      <div className="session-form">
        <p className="session-note">
          <strong>Asking {last.label}.</strong> Press Done asking (or Space) the moment you finish the question.
        </p>
        <button className="button-primary" type="button" onClick={endQuestion} autoFocus>
          Done asking
        </button>
      </div>
    )
  }

  const count = questions.length
  return (
    <form
      className="session-form"
      onSubmit={(event) => {
        event.preventDefault()
        ask()
      }}
    >
      <p className="session-note">Baseline recorded. Press Ask as you start each question.</p>
      <label className="field">
        <span>Question {count + 1} (optional text)</span>
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={200}
          autoComplete="off"
          placeholder="Where were you on Tuesday night?"
        />
        <small>Enter or the button asks it. Space anywhere else asks without text.</small>
      </label>
      <button className="button-primary" type="submit">
        Ask question
      </button>
    </form>
  )
}

function NewSession({ defaultName, connection, starting }) {
  const [name, setName] = useState(defaultName)
  const trimmed = name.trim()
  const offline = connection !== 'open'

  const submit = (event) => {
    event.preventDefault()
    if (trimmed && !offline && !starting) startSession(trimmed)
  }

  return (
    <form className="session-form" onSubmit={submit}>
      <label className="field">
        <span>Session name</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={40}
          autoComplete="off"
          spellCheck="false"
          aria-describedby="session-name-hint"
        />
        <small id="session-name-hint">
          {IS_DEMO ? "Don't use the subject's real name." : "Becomes the CSV file name. Don't use the subject's real name."}
        </small>
      </label>
      <button className="button-primary" type="submit" disabled={!trimmed || offline || starting}>
        {offline ? 'Backend offline' : starting ? 'Starting…' : 'Start session'}
      </button>
    </form>
  )
}

export default function SessionPanel({ state }) {
  const active = state.sessions.find((s) => s.id === state.activeId)
  const nextName = `Session ${state.sessions.length + 1}`

  return (
    <section className="card session" aria-label="Session">
      <h2 className="section-title">{active ? 'Session running' : 'New session'}</h2>
      {state.error && (
        <div className="notice notice-critical" role="alert">
          <span>{state.error}</span>
          <button type="button" className="notice-close" onClick={dismissError} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
      {active ? (
        <ActiveSession session={active} />
      ) : (
        // Remounts after each session so the name field picks up the next default.
        <NewSession key={nextName} defaultName={nextName} connection={state.connection} starting={state.starting} />
      )}
    </section>
  )
}
