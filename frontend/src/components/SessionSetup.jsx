import { useState } from 'react'
import { DEFAULT_SOURCE, startSession } from '../lib/store'

// CONTRIBUTING: recordings use subject01, subject02, ... and never real names.
const SUBJECT_PATTERN = /^subject\d+$/

export default function SessionSetup({ connection }) {
  const [subject, setSubject] = useState('subject01')
  const [truth, setTruth] = useState('')
  const [source, setSource] = useState(DEFAULT_SOURCE)

  const subjectId = subject.trim()
  const subjectValid = SUBJECT_PATTERN.test(subjectId)
  const backendUnavailable = source === 'backend' && connection !== 'open'

  const submit = (event) => {
    event.preventDefault()
    if (!subjectValid || backendUnavailable) return
    startSession({ subject: subjectId, truth: truth.trim(), source })
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2 className="section-title">New session</h2>

      <label className="field">
        <span>Subject ID</span>
        <input
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          autoComplete="off"
          spellCheck="false"
          aria-invalid={!subjectValid}
          aria-describedby="subject-hint"
        />
        <small id="subject-hint" className={subjectValid ? '' : 'field-error'}>
          {subjectValid ? 'No real names.' : 'Use the format subject01. No real names.'}
        </small>
      </label>

      <label className="field">
        <span>
          True item <em>optional</em>
        </span>
        <input
          value={truth}
          onChange={(event) => setTruth(event.target.value)}
          autoComplete="off"
          spellCheck="false"
          aria-describedby="truth-hint"
        />
        <small id="truth-hint">For measuring accuracy, never used for scoring. Leave empty in the live demo.</small>
      </label>

      <fieldset className="field">
        <legend>Run with</legend>
        <div className="segmented">
          <label>
            <input type="radio" name="source" checked={source === 'mock'} onChange={() => setSource('mock')} />
            <span>Simulated</span>
          </label>
          <label>
            <input type="radio" name="source" checked={source === 'backend'} onChange={() => setSource('backend')} />
            <span>Backend</span>
          </label>
        </div>
        <small>
          {source === 'mock'
            ? 'Trials and scores are generated in this browser. Scores are random.'
            : 'Sends a start message. The backend runs the timing and scoring.'}
        </small>
      </fieldset>

      <button className="button-primary" type="submit" disabled={!subjectValid || backendUnavailable}>
        {backendUnavailable ? 'Backend not connected' : 'Start session'}
      </button>
    </form>
  )
}
