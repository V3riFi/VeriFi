import { resetSession } from '../lib/store'
import { SimulatedBadge } from './SessionProgress'

function formatScore(score) {
  return `${score > 0 ? '+' : ''}${score.toFixed(2)}`
}

function ScoreBars({ scores, pick }) {
  const extent = Math.max(...scores.map(({ score }) => Math.abs(score)), 0.01)
  return (
    <ul className="scores" aria-label="Score per item, highest first">
      {scores.map(({ item, score }) => {
        const widthPct = (Math.abs(score) / extent) * 50
        const style = score >= 0 ? { left: '50%', width: `${widthPct}%` } : { right: '50%', width: `${widthPct}%` }
        return (
          <li key={item} className={item === pick ? 'score score-pick' : 'score'}>
            <span className="score-item">{item}</span>
            <span className="score-track" aria-hidden="true">
              <span className={`score-bar ${score >= 0 ? 'score-bar-up' : 'score-bar-down'}`} style={style} />
            </span>
            <span className="score-value">{formatScore(score)}</span>
          </li>
        )
      })}
    </ul>
  )
}

function TruthLine({ truth, pick }) {
  if (!truth) return null
  const matches = truth.toLowerCase() === pick.toLowerCase()
  return (
    <p className={`truth ${matches ? 'truth-match' : 'truth-miss'}`}>
      <span aria-hidden="true">{matches ? '✓' : '✕'}</span> True item was <strong>{truth}</strong>:{' '}
      {matches ? 'matches the strongest response' : 'does not match the strongest response'}
    </p>
  )
}

export default function Results({ session }) {
  const { scores, pick } = session.result
  const top = scores.find(({ item }) => item === pick)
  const runnerUp = scores.find(({ item }) => item !== pick)
  const lead = top && runnerUp ? top.score - runnerUp.score : null

  return (
    <div className="panel">
      <h2 className="section-title">
        Result{session.subject && ` · ${session.subject}`}
      </h2>
      {session.source === 'mock' && <SimulatedBadge />}

      <div className="pick">
        <span className="pick-label">Strongest response</span>
        <span className="pick-item">{pick}</span>
        {lead !== null && <span className="pick-detail">{lead.toFixed(2)} above the next item</span>}
      </div>

      <ScoreBars scores={scores} pick={pick} />
      <p className="axis-note">Score against this person's own responses. 0 is their average item.</p>

      <TruthLine truth={session.truth} pick={pick} />

      <p className="caveat">
        This shows arousal and recognition, not lying. Stress, surprise or a personal connection to an item can also cause
        a spike. A person makes the call.
      </p>

      <button className="button-primary" type="button" onClick={resetSession}>
        New session
      </button>
    </div>
  )
}
