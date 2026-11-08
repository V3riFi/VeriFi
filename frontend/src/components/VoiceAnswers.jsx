// Per-answer voice features from each "response" message (README: response.voice),
// with the voice baseline's reading as the row to compare against.

const fixed = (value, digits, unit = '') => (Number.isFinite(value) ? `${value.toFixed(digits)}${unit}` : '–')

function Latency({ voice }) {
  if (!Number.isFinite(voice.latency_s)) return '?'
  const text = fixed(voice.latency_s, 1, ' s')
  return voice.latency_marked ? (
    <strong>{text}</strong>
  ) : (
    <span title="Estimated: Done asking wasn't clicked">≈ {text}</span>
  )
}

function Cells({ voice, reading = false }) {
  return (
    <>
      <td>{reading ? '–' : <Latency voice={voice} />}</td>
      <td>{voice.cut ? `≥ ${fixed(voice.duration_s, 1, ' s')}` : fixed(voice.duration_s, 1, ' s')}</td>
      <td>{fixed(voice.speech_rate, 1)}</td>
      <td>{reading ? '–' : `${voice.pauses ?? '–'}${voice.pause_s ? ` (${voice.pause_s.toFixed(1)} s)` : ''}`}</td>
      <td>{fixed(voice.pitch, 0)}</td>
      <td>{fixed(voice.intonation, 0)}</td>
      <td>{fixed(voice.hnr, 1)}</td>
    </>
  )
}

function Row({ question }) {
  let content
  if (!question.responded) content = <td colSpan={7} className="answers-note">Listening…</td>
  else if (!question.voice) content = <td colSpan={7} className="answers-note">No voice data (microphone off)</td>
  else if (!Number.isFinite(question.voice.duration_s)) content = <td colSpan={7} className="answers-note">No answer heard</td>
  else content = <Cells voice={question.voice} />
  return (
    <tr>
      <th scope="row">{question.label}</th>
      {content}
    </tr>
  )
}

export default function VoiceAnswers({ session }) {
  if (!session) return <p className="answers-empty">Start a session to measure each answer.</p>
  const reading = session.voice?.reading
  if (!session.questions.length && !reading) {
    return <p className="answers-empty">Each answer's voice measurements appear here after its question.</p>
  }

  return (
    <div className="answers">
      <h3 className="answers-title">Answers</h3>
      <div className="answers-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Question</th>
              <th scope="col" className="answers-key">Response latency</th>
              <th scope="col">Length</th>
              <th scope="col">Speech rate <small>syl/s</small></th>
              <th scope="col">Pauses</th>
              <th scope="col">Pitch <small>Hz</small></th>
              <th scope="col">Intonation <small>± Hz</small></th>
              <th scope="col">HNR <small>dB</small></th>
            </tr>
          </thead>
          <tbody>
            {reading && (
              <tr className="answers-reading">
                <th scope="row">Reading</th>
                <Cells voice={reading} reading />
              </tr>
            )}
            {session.questions.map((question) => (
              <Row key={`${question.label}-${question.t}`} question={question} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="answers-help">
        Latency runs from <em>Done asking</em> to the first word (≈ when it wasn't clicked). Research links deception
        with a longer latency, shorter and faster answers, slightly higher pitch, flatter intonation and lower HNR.
        Compare answers with the reading and with each other, not with other people.
      </p>
    </div>
  )
}
