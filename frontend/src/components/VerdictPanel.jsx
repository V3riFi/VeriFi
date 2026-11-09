// Deception likelihood, answer by answer (backend/verdict.py, README: response.verdict).
// The newest answer large with an animated gauge (live while its window is open), the whole
// session so far, a timeline with one marker per answer, and earlier answers below.
import { answerKey, useAnswerVerdict } from '../lib/useAnswer'
import { useNow } from '../lib/useNow'
import { BANDS, INSUFFICIENT, bandCounts, bandInfo, percent, signed } from '../lib/verdict'

export function BandChip({ band, live = false }) {
  const info = bandInfo(band)
  return (
    <span className={`band-chip band-${info.key}`}>
      <span className="band-glyph" aria-hidden="true">
        {info.glyph}
      </span>
      {info.label}
      {live && <span className="band-live">live</span>}
    </span>
  )
}

// Shown beside the band when the answer's overall score is more than 2 SDs above the
// baseline mean (backend/verdict.py FALSE_Z).
export function FalseTag({ verdict }) {
  if (!verdict?.flaggedFalse) return null
  return (
    <span className="false-tag" title="Overall score more than 2 standard deviations above the baseline mean">
      False
    </span>
  )
}

/* ---------- gauge ---------- */

const CX = 100
const CY = 100
const R = 80

function point(fraction, radius = R) {
  const angle = Math.PI * (1 - fraction)
  return [CX + radius * Math.cos(angle), CY - radius * Math.sin(angle)]
}

function arc(from, to, radius = R) {
  const [x0, y0] = point(from, radius)
  const [x1, y1] = point(to, radius)
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${radius} ${radius} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`
}

const ZONES = [
  { key: 'low', from: 0, to: 0.3 },
  { key: 'unclear', from: 0.3, to: 0.6 },
  { key: 'elevated', from: 0.6, to: 0.85 },
  { key: 'high', from: 0.85, to: 1 },
]

export function Gauge({ verdict, label }) {
  const value = verdict?.likelihood ?? null
  const band = verdict?.band ?? INSUFFICIENT.key
  const scored = value !== null
  const shown = scored ? value : 0
  const [lo, hi] = verdict?.interval ?? [shown, shown]
  const text = scored
    ? `${label}: ${percent(value)} percent deception likelihood, ${bandInfo(band).label}${verdict.provisional ? ', live estimate' : ''}`
    : `${label}: ${verdict ? 'not enough signal' : 'waiting for the answer'}`
  return (
    <div
      className={`gauge${verdict?.provisional ? ' gauge-live' : ''}`}
      role="meter"
      aria-label={text}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={scored ? Math.round(value * 100) : undefined}
      aria-valuetext={text}
    >
      <svg viewBox="0 0 200 112" aria-hidden="true">
        {ZONES.map((zone) => (
          <path key={zone.key} className={`gauge-zone gauge-zone-${zone.key}`} d={arc(zone.from, zone.to)} />
        ))}
        {scored && <path className="gauge-interval" d={arc(Math.max(0.001, lo), Math.min(0.999, Math.max(hi, lo + 0.002)), R + 13)} />}
        <path
          className={`gauge-value band-stroke-${band}`}
          d={arc(0, 1)}
          pathLength="100"
          style={{ strokeDashoffset: 100 - shown * 100 }}
        />
        <g className="gauge-needle" style={{ transform: `rotate(${shown * 180}deg)` }}>
          <line x1={CX - R - 10} y1={CY} x2={CX - R + 16} y2={CY} />
        </g>
      </svg>
      <div className="gauge-readout" aria-hidden="true">
        <strong>{scored ? percent(value) : '–'}</strong>
        {scored && <span>%</span>}
      </div>
    </div>
  )
}

/* ---------- pieces ---------- */

function Countdown({ question }) {
  const now = useNow(250)
  if (question.responded || !Number.isFinite(question.dueT)) return null
  const span = question.dueT - question.t
  const left = question.dueT - now
  const progress = span > 0 ? Math.min(1, Math.max(0, 1 - left / span)) : 1
  return (
    <div className="verdict-countdown">
      <span>
        <span className="pulse-dot" aria-hidden="true" />
        Analyzing · {left > 0.5 ? `verdict in ${Math.ceil(left)} s` : 'finishing…'}
      </span>
      <div
        className="progress"
        role="progressbar"
        aria-label={`${question.label} reaction window`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
      >
        <span style={{ width: `${progress * 100}%` }} />
      </div>
    </div>
  )
}

// One row per weighted signal group: how far it sits from calm (z, in SD), as a centered bar.
function CueBars({ verdict }) {
  const cats = (verdict?.categories ?? []).filter((c) => c.weight > 0)
  if (!cats.length) return null
  return (
    <ul className="cue-bars" aria-label="Signal groups, in standard deviations from calm">
      {cats.map((cat) => {
        const z = cat.z
        const width = Number.isFinite(z) ? (Math.min(Math.abs(z), 4) / 4) * 50 : 0
        return (
          <li key={cat.name}>
            <span className="cue-name">{cat.name}</span>
            <span className="cue-track" aria-hidden="true">
              <span
                className={`cue-fill ${Number.isFinite(z) && z >= 0 ? 'cue-up' : 'cue-down'}`}
                style={{ left: `${Number.isFinite(z) && z < 0 ? 50 - width : 50}%`, width: `${width}%` }}
              />
            </span>
            <span className="cue-value" title={cat.missing.join('\n') || undefined}>
              {Number.isFinite(z) ? `${signed(z)} SD` : 'no data'}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function CurrentAnswer({ session, question, onSelect }) {
  const verdict = useAnswerVerdict(session, question)
  const band = verdict?.band ?? null
  const info = band ? bandInfo(band) : null
  return (
    <div className={`verdict-current${band ? ` verdict-current-${band}` : ''}`}>
      <p className="eyebrow">Latest answer</p>
      <p className="verdict-question">
        <strong>{question.label}</strong>
        {question.text && <q>{question.text}</q>}
      </p>
      <div className="verdict-current-body">
        <Gauge verdict={verdict} label={question.label} />
        <div className="verdict-current-text">
          {verdict ? (
            <span className="verdict-chips">
              <BandChip band={verdict.band} live={verdict.provisional} />
              <FalseTag verdict={verdict} />
            </span>
          ) : (
            <span className="tile-note">Waiting for data…</span>
          )}
          {verdict?.likelihood !== null && verdict?.interval && (
            <span className="verdict-interval">
              80% range {percent(verdict.interval[0])}–{percent(verdict.interval[1])}%
            </span>
          )}
          <p className="verdict-plain">
            {verdict?.band === INSUFFICIENT.key ? verdict.reason ?? INSUFFICIENT.plain : info?.plain ?? 'The reading builds up as the answer comes in.'}
          </p>
          <Countdown question={question} />
        </div>
      </div>
      <CueBars verdict={verdict} />
      <button type="button" className="button-link verdict-why" onClick={() => onSelect(answerKey(question))}>
        Why this reading →
      </button>
    </div>
  )
}

function SessionMeter({ session, selectedKey, onSelect }) {
  const sv = session.verdict
  const scored = Number.isFinite(sv?.likelihood)
  const value = scored ? sv.likelihood : 0
  const [lo, hi] = sv?.interval ?? [value, value]
  const text = scored
    ? `Whole session: ${percent(value)} percent, ${bandInfo(sv.band).label}, from ${sv.answers} answers`
    : 'Whole session: no scored answers yet'
  return (
    <div className="verdict-session">
      <p className="eyebrow">Whole session so far</p>
      <div className="session-meter-head">
        <strong className="session-meter-value">{scored ? `${percent(value)}%` : '–'}</strong>
        {sv && <BandChip band={sv.band} />}
      </div>
      <div
        className="session-meter"
        role="meter"
        aria-label={text}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={scored ? Math.round(value * 100) : undefined}
        aria-valuetext={text}
      >
        {ZONES.map((zone) => (
          <span
            key={zone.key}
            className={`meter-zone gauge-zone-${zone.key}`}
            style={{ left: `${zone.from * 100}%`, width: `${(zone.to - zone.from) * 100}%` }}
          />
        ))}
        {scored && <span className="meter-interval" style={{ left: `${lo * 100}%`, width: `${Math.max(0.5, (hi - lo) * 100)}%` }} />}
        {scored && <span className={`meter-marker band-bg-${sv.band}`} style={{ left: `${value * 100}%` }} />}
      </div>
      <p className="session-meter-note">
        {scored
          ? `Evidence from ${sv.answers} scored answer${sv.answers === 1 ? '' : 's'}, added up. Updates after every answer.`
          : 'Fills in as answers are scored.'}
      </p>
      <Timeline session={session} selectedKey={selectedKey} onSelect={onSelect} />
    </div>
  )
}

function Timeline({ session, selectedKey, onSelect }) {
  if (!session.questions.length) return null
  return (
    <ol className="verdict-timeline" aria-label="Answers in the order asked">
      {session.questions.map((q) => {
        const key = answerKey(q)
        const band = q.verdict?.band
        const info = band ? bandInfo(band) : null
        const label = q.verdict
          ? `${q.label}: ${info.label}${Number.isFinite(q.verdict.likelihood) ? `, ${percent(q.verdict.likelihood)}%` : ''}`
          : `${q.label}: being analyzed`
        return (
          <li key={key}>
            <button
              type="button"
              className={`timeline-dot ${band ? `band-bg-${band}` : 'timeline-pending'}`}
              aria-label={label}
              title={label}
              aria-pressed={selectedKey === key}
              onClick={() => onSelect(key)}
            >
              <span aria-hidden="true">{info ? info.glyph : '…'}</span>
            </button>
            <span className="timeline-label" aria-hidden="true">
              {q.label}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

function EarlierRow({ session, question, selected, onSelect }) {
  const verdict = useAnswerVerdict(session, question)
  const value = verdict?.likelihood
  return (
    <li>
      <button type="button" className="verdict-row" aria-pressed={selected} onClick={() => onSelect(answerKey(question))}>
        <span className="verdict-row-q">
          <strong>{question.label}</strong>
          {question.text && <q>{question.text}</q>}
        </span>
        {verdict ? (
          <span className="verdict-chips">
            <BandChip band={verdict.band} live={verdict.provisional} />
            <FalseTag verdict={verdict} />
          </span>
        ) : (
          <span className="tile-note">Analyzing…</span>
        )}
        <span className="verdict-row-value">
          <span className="mini-meter" aria-hidden="true">
            {Number.isFinite(value) && <span className={`band-bg-${verdict.band}`} style={{ width: `${value * 100}%` }} />}
          </span>
          {Number.isFinite(value) ? `${percent(value)}%` : '–'}
        </span>
      </button>
    </li>
  )
}

function Counts({ questions }) {
  const counts = bandCounts(questions)
  const shown = [...BANDS, INSUFFICIENT].filter((b) => counts[b.key] > 0)
  if (!shown.length) return null
  return (
    <ul className="verdict-counts" aria-label="Answers per result">
      {shown.map((b) => (
        <li key={b.key} className={`band-chip band-${b.key}`}>
          <span className="band-glyph" aria-hidden="true">
            {b.glyph}
          </span>
          {b.label} <strong>{counts[b.key]}</strong>
        </li>
      ))}
    </ul>
  )
}

function Empty({ children }) {
  return <p className="verdict-empty">{children}</p>
}

export default function VerdictPanel({ session, selectedKey, onSelect }) {
  let body
  if (!session) {
    body = <Empty>Start a session. Each answer gets a deception likelihood here, about 10 seconds after it is asked.</Empty>
  } else if (!session.baseline) {
    body = <Empty>Recording the calm baseline. Answers are compared with it, so questions start once it's done.</Empty>
  } else if (session.voice && !session.voice.done) {
    body = <Empty>Recording the voice baseline. Questions start once the subject has finished reading.</Empty>
  } else if (!session.questions.length) {
    body = <Empty>Ask the first question. Its reading builds up live and settles about 10 seconds later.</Empty>
  } else {
    const newest = session.questions[session.questions.length - 1]
    const earlier = session.questions.slice(0, -1).reverse()
    body = (
      <>
        <div className="verdict-top">
          <CurrentAnswer session={session} question={newest} onSelect={onSelect} />
          <SessionMeter session={session} selectedKey={selectedKey} onSelect={onSelect} />
        </div>
        {earlier.length > 0 && (
          <div className="verdict-earlier">
            <h3 className="eyebrow">Earlier answers</h3>
            <ol>
              {earlier.map((q) => (
                <EarlierRow
                  key={answerKey(q)}
                  session={session}
                  question={q}
                  selected={selectedKey === answerKey(q)}
                  onSelect={onSelect}
                />
              ))}
            </ol>
          </div>
        )}
      </>
    )
  }

  return (
    <section className="card verdicts" aria-label="Deception likelihood per answer">
      <div className="card-head verdicts-head">
        <div>
          <h2 className="section-title">Deception likelihood</h2>
          <p className="verdicts-sub">Answer by answer, against this person’s own calm baseline</p>
        </div>
        {session && <Counts questions={session.questions} />}
      </div>
      {body}
      <p className="verdicts-caveat">
        This measures arousal compared with this person’s calm baseline, not lying itself. Use a high reading as a
        reason to follow up, never as proof.
      </p>
    </section>
  )
}
