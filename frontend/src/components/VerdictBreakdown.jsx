// "Why this reading": the statistics behind one answer's verdict, in plain words and pictures.
// What moved the score (an additive waterfall: each signal group's weight × z adds up exactly to
// the final score), how unusual each group was compared with calm moments (histograms of
// pseudo-questions across the baseline), what the sensors did around the question, how the
// answers compare (and add up), and the quality checks. Live while the answer is open.
import { useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import { answerKey, useAnswerVerdict } from '../lib/useAnswer'
import { useNow } from '../lib/useNow'
import { INSUFFICIENT, bandInfo, percent, signed } from '../lib/verdict'
import Section from './Section'
import { BandChip } from './VerdictPanel'

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

function useWidth(ref) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element) return undefined
    const observer = new ResizeObserver(() => setWidth(element.clientWidth))
    observer.observe(element)
    setWidth(element.clientWidth)
    return () => observer.disconnect()
  }, [ref])
  return width
}

// Redraws charts drawn in canvas when the light/dark theme changes.
function useThemeVersion() {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1)
    const scheme = window.matchMedia('(prefers-color-scheme: dark)')
    scheme.addEventListener('change', bump)
    const observer = new MutationObserver(bump)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      scheme.removeEventListener('change', bump)
      observer.disconnect()
    }
  }, [])
  return version
}

const joinNames = (names) =>
  names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`

function summary(question, verdict) {
  if (!verdict) return `${question.label} is being analyzed. The reading builds up as the answer comes in.`
  if (verdict.band === INSUFFICIENT.key) {
    return `${question.label}: not enough signal to judge. ${verdict.reason ?? ''}`.trim()
  }
  const cats = verdict.categories.filter((c) => Number.isFinite(c.contribution))
  const up = cats.filter((c) => c.contribution > 0.1).sort((a, b) => b.contribution - a.contribution).slice(0, 2)
  const down = cats.filter((c) => c.contribution < -0.1).sort((a, b) => a.contribution - b.contribution).slice(0, 1)
  const lead = `${question.label} reads ${bandInfo(verdict.band).label.toLowerCase()} (${percent(verdict.likelihood)}% likelihood${verdict.provisional ? ' so far' : ''}). `
  const why = up.length
    ? `${joinNames(up.map((c) => c.name))} moved furthest from calm`
    : 'No signal group moved much from calm'
  const against = down.length ? `, while ${joinNames(down.map((c) => c.name))} pulled toward calm.` : '.'
  return lead + why + against
}

/* ---------- 1. What moved the score: the waterfall ---------- */

function Waterfall({ verdict }) {
  const cats = verdict.categories
    .filter((c) => Number.isFinite(c.contribution) && c.weight > 0)
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
  if (!cats.length) return <p className="why-empty">No signal group could be scored for this answer.</p>
  // Each step starts where the previous one ended.
  const steps = cats.reduce((acc, c) => {
    const from = acc.length ? acc[acc.length - 1].to : 0
    return [...acc, { ...c, from, to: from + c.contribution }]
  }, [])
  const total = verdict.z ?? steps[steps.length - 1].to
  const ends = [0, total, ...steps.flatMap((s) => [s.from, s.to])]
  const min = Math.min(-1, ...ends) - 0.3
  const max = Math.max(2.5, ...ends) + 0.3
  const pos = (v) => ((v - min) / (max - min)) * 100
  const totalWeight = cats.reduce((sum, c) => sum + c.weight, 0) || 1
  return (
    <div className="waterfall">
      <ol aria-label="Each signal group's push on the score, from 0 (a calm answer) to the final score">
        <li className="wf-row wf-start">
          <span className="wf-label">Calm expectation</span>
          <span className="wf-track" aria-hidden="true">
            <span className="wf-zero" style={{ left: `${pos(0)}%` }} />
          </span>
          <span className="wf-value">0.00</span>
        </li>
        {steps.map((s) => (
          <li key={s.name} className="wf-row">
            <span className="wf-label">
              {s.name}
              <small>{Math.round((s.weight / totalWeight) * 100)}% weight</small>
            </span>
            <span className="wf-track" aria-hidden="true">
              <span className="wf-zero" style={{ left: `${pos(0)}%` }} />
              <span
                className={`wf-bar ${s.contribution >= 0 ? 'wf-up' : 'wf-down'}`}
                style={{ left: `${pos(Math.min(s.from, s.to))}%`, width: `${Math.max(0.6, Math.abs(pos(s.to) - pos(s.from)))}%` }}
              />
            </span>
            <span className="wf-value">
              <span className="sr-only">{s.contribution >= 0 ? 'pushes up' : 'pushes down'} by </span>
              {signed(s.contribution, 2)}
            </span>
          </li>
        ))}
        <li className="wf-row wf-total">
          <span className="wf-label">Final score</span>
          <span className="wf-track" aria-hidden="true">
            <span className="wf-zero" style={{ left: `${pos(0)}%` }} />
            <span
              className={`wf-bar band-bg-${verdict.band}`}
              style={{ left: `${pos(Math.min(0, total))}%`, width: `${Math.max(0.6, Math.abs(pos(total) - pos(0)))}%` }}
            />
          </span>
          <span className="wf-value">{signed(total, 2)}</span>
        </li>
      </ol>
      <p className="why-note">
        {Number.isFinite(verdict.likelihood)
          ? `A final score of ${signed(total, 2)} turns into a ${percent(verdict.likelihood)}% deception likelihood (80% range ${percent(verdict.interval?.[0])}–${percent(verdict.interval?.[1])}%).`
          : 'This answer failed a quality check, so the score is not turned into a likelihood.'}{' '}
        <span className="wf-key">
          <span className="wf-swatch wf-up" aria-hidden="true" /> toward a reaction{' '}
          <span className="wf-swatch wf-down" aria-hidden="true" /> toward calm
        </span>
      </p>
    </div>
  )
}

/* ---------- 2. Compared with calm moments: histograms ---------- */

function Histogram({ name, dist, value }) {
  const { edges, counts } = dist
  const lo = edges[0]
  const hi = edges[edges.length - 1]
  const tallest = Math.max(1, ...counts)
  const at = Number.isFinite(value) ? ((Math.min(hi, Math.max(lo, value)) - lo) / (hi - lo)) * 100 : null
  const share = dist.percentile
  let words = 'no comparison'
  if (Number.isFinite(share)) {
    if (share >= 0.995) words = 'higher than every calm moment'
    else if (share <= 0.005) words = 'lower than every calm moment'
    else if (share > 0.75) words = `higher than ${Math.round(share * 100)}% of calm moments`
    else if (share < 0.25) words = `lower than ${Math.round((1 - share) * 100)}% of calm moments`
    else words = 'typical of calm moments'
  }
  return (
    <figure className="hist" aria-label={`${name}: this answer is ${words} (${dist.n} calm moments)`}>
      <figcaption>
        <span>{name}</span>
        <small>{Number.isFinite(value) ? `${signed(value)} SD` : '–'}</small>
      </figcaption>
      <div className="hist-plot" aria-hidden="true">
        <svg viewBox="0 0 100 40" preserveAspectRatio="none">
          {counts.map((count, i) => {
            const x = ((edges[i] - lo) / (hi - lo)) * 100
            const w = ((edges[i + 1] - edges[i]) / (hi - lo)) * 100
            const h = (count / tallest) * 36
            return <rect key={edges[i]} className="hist-bar" x={x + 0.4} width={Math.max(0, w - 0.8)} y={40 - h} height={h} />
          })}
        </svg>
        {at !== null && <span className="hist-marker" style={{ left: `${at}%` }} />}
      </div>
      <p className="hist-words">This answer: {words}</p>
    </figure>
  )
}

function CalmComparison({ verdict }) {
  const cats = verdict.categories.filter((c) => c.null && c.null.n > 0 && Number.isFinite(c.z))
  if (!cats.length) return <p className="why-empty">No calm-moment comparison for this answer.</p>
  return (
    <>
      <div className="hist-grid">
        {verdict.nullTotal && Number.isFinite(verdict.z) && (
          <Histogram name="Final score" dist={verdict.nullTotal} value={verdict.z} />
        )}
        {cats.map((c) => (
          <Histogram key={c.name} name={c.name} dist={c.null} value={c.z} />
        ))}
      </div>
      <p className="why-note">
        Grey bars: the same measurement taken at {cats[0].null.n} moments during the calm baseline, when nothing was
        asked. The line is this answer.
      </p>
    </>
  )
}

/* ---------- 3. What the sensors did: traces ---------- */

const WINDOW_COLORS = { pre: '--band', answer: '--band-normal' }

function TraceChart({ series, trace, themeVersion }) {
  const ref = useRef(null)
  useEffect(() => {
    const container = ref.current
    const xs = series.points.map((p) => p[0])
    const ys = series.points.map((p) => p[1])
    const color = (name) => cssVar(name)
    const opts = {
      width: container.clientWidth,
      height: 120,
      padding: [8, 8, 0, 0],
      legend: { show: false },
      cursor: { show: false },
      scales: {
        x: { time: false, range: () => [trace.from, trace.to] },
        y: {
          range: (u, min, max) => {
            const span = Math.max(max - min, Math.abs(max) * 0.02, 1e-6)
            return [min - span * 0.15, max + span * 0.15]
          },
        },
      },
      axes: [
        {
          stroke: () => color('--text-muted'),
          grid: { stroke: () => color('--grid'), width: 1 },
          ticks: { show: false },
          font: '11px system-ui, sans-serif',
          size: 24,
          splits: () => [-10, -5, 0, 5, 10],
          values: (u, splits) => splits.map((s) => (s === 0 ? 'Ask' : `${s > 0 ? '+' : ''}${s}s`)),
        },
        {
          stroke: () => color('--text-muted'),
          grid: { stroke: () => color('--grid'), width: 1 },
          ticks: { show: false },
          font: '11px system-ui, sans-serif',
          size: 44,
          values: (u, splits) => splits.map((v) => (Math.abs(v) >= 100 ? Math.round(v) : v.toFixed(1))),
        },
      ],
      series: [{}, { stroke: () => color('--accent'), width: 2, points: { show: false } }],
      hooks: {
        drawClear: [
          (u) => {
            const { ctx } = u
            const { left, top, width, height } = u.bbox
            ctx.save()
            ctx.beginPath()
            ctx.rect(left, top, width, height)
            ctx.clip()
            for (const w of trace.windows) {
              const fill = WINDOW_COLORS[w.key]
              if (!fill) continue
              const x0 = u.valToPos(w.from, 'x', true)
              const x1 = u.valToPos(w.to, 'x', true)
              ctx.fillStyle = color(fill)
              ctx.fillRect(x0, top, x1 - x0, height)
            }
            ctx.restore()
          },
        ],
        draw: [
          (u) => {
            const { ctx } = u
            const { top, height } = u.bbox
            const px = uPlot.pxRatio
            const line = (t, dash) => {
              const x = Math.round(u.valToPos(t, 'x', true)) + 0.5
              ctx.save()
              ctx.strokeStyle = color('--text-secondary')
              ctx.lineWidth = px * 1.5
              ctx.setLineDash(dash.map((d) => d * px))
              ctx.beginPath()
              ctx.moveTo(x, top)
              ctx.lineTo(x, top + height)
              ctx.stroke()
              ctx.restore()
            }
            line(0, [])
            if (Number.isFinite(trace.end)) line(trace.end, [4, 3])
          },
        ],
      },
    }
    const u = new uPlot(opts, [xs, ys], container)
    const resize = new ResizeObserver(() => {
      if (container.clientWidth !== u.width) u.setSize({ width: container.clientWidth, height: 120 })
    })
    resize.observe(container)
    return () => {
      resize.disconnect()
      u.destroy()
    }
  }, [series, trace, themeVersion])

  const values = series.points.map((p) => p[1])
  const before = series.points.filter((p) => p[0] < 0).map((p) => p[1])
  const after = series.points.filter((p) => p[0] >= 0).map((p) => p[1])
  const avg = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length
  const described =
    before.length && after.length
      ? `${series.label}: averaged ${avg(before).toFixed(1)} ${series.unit} before the question and ${avg(after).toFixed(1)} after, peaking at ${Math.max(...after).toFixed(1)}.`
      : `${series.label}: ${values.length} readings.`
  return (
    <figure className="trace" aria-label={described}>
      <figcaption>
        {series.label} <small>{series.unit}</small>
      </figcaption>
      <div ref={ref} aria-hidden="true" />
    </figure>
  )
}

function Traces({ verdict }) {
  const themeVersion = useThemeVersion()
  const trace = verdict.trace
  if (!trace || !trace.series.length) return <p className="why-empty">No sensor data around this question.</p>
  return (
    <>
      <div className="trace-grid">
        {trace.series.map((series) => (
          <TraceChart key={series.key} series={series} trace={trace} themeVersion={themeVersion} />
        ))}
      </div>
      <ul className="trace-key" aria-label="Chart key">
        <li>
          <span className="trace-swatch" style={{ background: 'var(--band)' }} aria-hidden="true" /> 2 s before: the
          reference
        </li>
        <li>
          <span className="trace-swatch" style={{ background: 'var(--band-normal)' }} aria-hidden="true" /> Answer window
        </li>
        <li>
          <span className="trace-line" aria-hidden="true" /> Ask
        </li>
        {Number.isFinite(trace.end) && (
          <li>
            <span className="trace-line trace-line-dashed" aria-hidden="true" /> Done asking
          </li>
        )}
      </ul>
    </>
  )
}

/* ---------- 4. How the answers compare ---------- */

const ZONE_LINES = [
  { key: 'low', label: 'Low', from: 0, to: 0.3 },
  { key: 'unclear', label: 'Unclear', from: 0.3, to: 0.6 },
  { key: 'elevated', label: 'Elevated', from: 0.6, to: 0.85 },
  { key: 'high', label: 'High', from: 0.85, to: 1 },
]

// Each answer's own likelihood (dots, with the 80% range as a whisker), joined by a line, and
// the whole session's running total as a thin dashed line. Bands labelled on the right.
function AnswersChart({ session, selectedLabel }) {
  const ref = useRef(null)
  const width = useWidth(ref)
  const questions = session.questions
  const sessionAfter = new Map((session.verdict?.history ?? []).map((h) => [h.label, h.likelihood]))
  const rows = questions.map((q, i) => ({
    i,
    label: q.label,
    verdict: q.responded ? q.verdict : null,
    session: sessionAfter.get(q.label) ?? null,
  }))
  const scored = rows.filter((r) => Number.isFinite(r.verdict?.likelihood))

  const height = 172
  const pad = { l: 30, r: 64, t: 10, b: 22 }
  const w = Math.max(0, width - pad.l - pad.r)
  const h = height - pad.t - pad.b
  const x = (i) => pad.l + (questions.length <= 1 ? w / 2 : (i / (questions.length - 1)) * w)
  const y = (p) => pad.t + (1 - p) * h

  // The answer line breaks where an answer had no reading.
  const segments = []
  let current = []
  for (const r of rows) {
    if (Number.isFinite(r.verdict?.likelihood)) current.push(r)
    else if (current.length) {
      segments.push(current)
      current = []
    }
  }
  if (current.length) segments.push(current)
  const sessionPoints = rows.filter((r) => Number.isFinite(r.session))

  const strongest = scored.reduce((best, r) => (best && best.verdict.likelihood >= r.verdict.likelihood ? best : r), null)
  const sv = session.verdict
  const description = strongest
    ? `${strongest.label} stands out most (${percent(strongest.verdict.likelihood)}%, ${bandInfo(strongest.verdict.band).label}). ` +
      (Number.isFinite(sv?.likelihood)
        ? `All answers together: ${percent(sv.likelihood)}% (${bandInfo(sv.band).label}).`
        : '')
    : 'Each answer appears here once it is scored.'

  return (
    <div className="evidence" ref={ref}>
      {width > 0 && questions.length > 0 && (
        <svg width={width} height={height} role="img" aria-label={`Deception likelihood of each answer. ${description}`}>
          {ZONE_LINES.map((z) => (
            <g key={z.key}>
              <rect className={`gauge-zone-fill-${z.key}`} x={pad.l} width={w} y={y(z.to)} height={y(z.from) - y(z.to)} />
              <text className={`evidence-band band-text-${z.key}`} x={pad.l + w + 8} y={(y(z.from) + y(z.to)) / 2 + 3.5}>
                {z.label}
              </text>
            </g>
          ))}
          {[0, 50, 100].map((p) => (
            <text key={p} className="evidence-axis" x={pad.l - 6} y={y(p / 100) + 3.5} textAnchor="end">
              {p}
            </text>
          ))}
          {rows.map((r) =>
            r.label === selectedLabel ? (
              <line key="sel" className="evidence-selected" x1={x(r.i)} x2={x(r.i)} y1={pad.t} y2={pad.t + h} />
            ) : null,
          )}
          {sessionPoints.length > 1 && (
            <path
              className="evidence-session"
              d={`M ${sessionPoints.map((r) => `${x(r.i)} ${y(r.session)}`).join(' L ')}`}
            />
          )}
          {segments.map((seg) =>
            seg.length > 1 ? (
              <path
                key={seg[0].label}
                className="evidence-line"
                d={`M ${seg.map((r) => `${x(r.i)} ${y(r.verdict.likelihood)}`).join(' L ')}`}
              />
            ) : null,
          )}
          {scored.map((r) => {
            const [lo, hi] = r.verdict.interval ?? [r.verdict.likelihood, r.verdict.likelihood]
            return (
              <g key={`${r.label}-${r.i}`}>
                <line className="evidence-whisker" x1={x(r.i)} x2={x(r.i)} y1={y(lo)} y2={y(hi)} />
                <line className="evidence-whisker" x1={x(r.i) - 4} x2={x(r.i) + 4} y1={y(lo)} y2={y(lo)} />
                <line className="evidence-whisker" x1={x(r.i) - 4} x2={x(r.i) + 4} y1={y(hi)} y2={y(hi)} />
                <circle
                  className={`evidence-point band-fill-${r.verdict.band}`}
                  cx={x(r.i)}
                  cy={y(r.verdict.likelihood)}
                  r={r.label === selectedLabel ? 6.5 : 5}
                />
              </g>
            )
          })}
          {rows.map((r) => (
            <text
              key={`${r.label}-${r.i}-x`}
              className={`evidence-axis${r.label === selectedLabel ? ' evidence-axis-selected' : ''}`}
              x={x(r.i)}
              y={height - 6}
              textAnchor="middle"
            >
              {r.label}
            </text>
          ))}
        </svg>
      )}
      <ul className="evidence-legend" aria-hidden="true">
        <li>
          <span className="legend-dot" /> Each answer, with its 80% range
        </li>
        <li>
          <span className="legend-dash" /> All answers together
        </li>
      </ul>
      <p className="why-note">{description}</p>
      <table className="sr-only">
        <caption>Deception likelihood of each answer</caption>
        <thead>
          <tr>
            <th scope="col">Answer</th>
            <th scope="col">Result</th>
            <th scope="col">This answer</th>
            <th scope="col">All answers so far</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.label}-${r.i}`}>
              <th scope="row">{r.label}</th>
              <td>{r.verdict ? bandInfo(r.verdict.band).label : 'Being analyzed'}</td>
              <td>{Number.isFinite(r.verdict?.likelihood) ? `${percent(r.verdict.likelihood)}%` : '–'}</td>
              <td>{Number.isFinite(r.session) ? `${percent(r.session)}%` : '–'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---------- 5. Quality checks ---------- */

function Checks({ verdict }) {
  const failed = verdict.gates.filter((g) => !g.pass)
  const total = verdict.gates.length
  return (
    <>
      <p className={`checks-summary ${failed.length ? 'checks-summary-fail' : 'checks-summary-pass'}`}>
        <span className="check-icon" aria-hidden="true">
          {failed.length ? '✕' : '✓'}
        </span>
        {failed.length
          ? `${failed.length} of ${total} checks failed, so this answer shows “Not enough signal”.`
          : `All ${total} checks passed${verdict.provisional ? ' so far' : ''}: this reading is based on good data.`}
      </p>
      <ul className="checks">
        {verdict.gates.map((g) => (
          <li key={g.key} className={g.pass ? 'check-pass' : 'check-fail'}>
            <span className="check-icon" aria-hidden="true">
              {g.pass ? '✓' : '✕'}
            </span>
            <span>
              <strong>{g.label}</strong>
              <span className="sr-only">{g.pass ? ': passed' : ': failed'}</span>
              {g.detail && <small>{g.detail}</small>}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}

/* ---------- 6. The numbers ---------- */

const fixed = (v, d = 3) => (Number.isFinite(v) ? v.toFixed(d) : '–')
const ARROWS = { 1: '↑', [-1]: '↓', 0: '↕' }

function Numbers({ verdict }) {
  const m = verdict.model ?? {}
  const lr = Number.isFinite(verdict.logLr) ? Math.exp(verdict.logLr) : null
  return (
    <details className="why-numbers">
      <summary>The numbers behind this reading</summary>
      <dl className="numbers-model">
        <div>
          <dt>Score Z (weighted Stouffer)</dt>
          <dd>{fixed(verdict.z)}</dd>
        </div>
        <div>
          <dt>Z used (floored at −λ/σ²)</dt>
          <dd>{fixed(verdict.zEff)}</dd>
        </div>
        <div>
          <dt>√Σw² (normalizer)</dt>
          <dd>{fixed(verdict.norm)}</dd>
        </div>
        <div>
          <dt>log likelihood ratio</dt>
          <dd>{fixed(verdict.logLr)}</dd>
        </div>
        <div>
          <dt>Likelihood ratio</dt>
          <dd>{lr === null ? '–' : lr >= 1000 ? lr.toExponential(2) : lr.toFixed(3)}</dd>
        </div>
        <div>
          <dt>Model λ · σ · prior</dt>
          <dd>
            {fixed(m.lambda, 2)} · {fixed(m.sigma, 2)} · {fixed(m.prior, 2)}
          </dd>
        </div>
      </dl>
      <div className="numbers-scroll">
        <table className="numbers-table">
          <thead>
            <tr>
              <th scope="col">Signal</th>
              <th scope="col">Dir</th>
              <th scope="col">Weight</th>
              <th scope="col">Reference</th>
              <th scope="col">This answer</th>
              <th scope="col">z</th>
              <th scope="col">p</th>
            </tr>
          </thead>
          {verdict.categories.map((c) => (
            <tbody key={c.name}>
              <tr className="numbers-cat">
                <th scope="rowgroup" colSpan={2}>
                  {c.name} <small>{c.method === 'stouffer' ? 'one-sided, Stouffer' : c.method === 'chi2' ? 'chi-square' : ''}</small>
                </th>
                <td>{fixed(c.weight, 2)}</td>
                <td colSpan={2}>contribution {signed(c.contribution, 3)}</td>
                <td>{fixed(c.z)}</td>
                <td>{Number.isFinite(c.suspicion) ? fixed(1 - c.suspicion, 4) : '–'}</td>
              </tr>
              {(c.signals ?? []).map((s) => (
                <tr key={s.key}>
                  <th scope="row">{s.name}</th>
                  <td>{ARROWS[s.direction]}</td>
                  <td>{fixed(s.weight, 2)}</td>
                  <td>
                    {fixed(s.base, 2)} ± {fixed(s.baseSd, 2)}
                    {(s.phasic || s.delta) && <small> before</small>}
                  </td>
                  <td>
                    {fixed(s.now, 2)}
                    {s.phasic ? <small> peak</small> : ` ± ${fixed(s.nowSd, 2)}`}
                  </td>
                  <td>{fixed(s.z)}</td>
                  <td>{fixed(s.p, 4)}</td>
                </tr>
              ))}
              {!c.signals?.length && c.missing.length > 0 && (
                <tr>
                  <td colSpan={7} className="numbers-missing">
                    {c.missing.join('; ')}
                  </td>
                </tr>
              )}
            </tbody>
          ))}
        </table>
      </div>
    </details>
  )
}

/* ---------- the block ---------- */

function Panel({ title, hint, wide = false, children }) {
  return (
    <section className={`why-panel${wide ? ' why-wide' : ''}`}>
      <h3>{title}</h3>
      {hint && <p className="why-hint">{hint}</p>}
      {children}
    </section>
  )
}

function Waiting({ question }) {
  const now = useNow(500)
  const left = Number.isFinite(question.dueT) ? Math.max(0, Math.ceil(question.dueT - now)) : null
  return <p className="why-empty">Shown when the answer is scored{left ? `, in ${left} s` : ''}.</p>
}

// A dropdown, closed by default: its summary line names the answer and its result.
export default function VerdictBreakdown({ session, selectedKey, onSelect, open, onToggle }) {
  if (!session?.baseline || !session.questions.length) return null
  const questions = session.questions
  // The answer picked in the panel above, otherwise the newest one.
  const found = questions.findIndex((q) => answerKey(q) === selectedKey)
  const selected = found === -1 ? questions.length - 1 : found
  return (
    <Breakdown
      session={session}
      question={questions[selected]}
      previous={questions[selected - 1] ?? null}
      next={questions[selected + 1] ?? null}
      onSelect={onSelect}
      open={open}
      onToggle={onToggle}
    />
  )
}

function Breakdown({ session, question, previous, next, onSelect, open, onToggle }) {
  const verdict = useAnswerVerdict(session, question)
  const final = question.responded && verdict && !verdict.provisional
  const status = (
    <>
      <span>{question.label}</span>
      {verdict ? <BandChip band={verdict.band} live={verdict.provisional} /> : <span>being analyzed</span>}
      <span className="section-status-hint">the statistics behind it</span>
    </>
  )
  return (
    <Section id="verdict-breakdown" className="why" icon="why" title="Why this reading" status={status} open={open} onToggle={onToggle}>
      <div className="why-head">
        <div className="why-heading">
          <strong>{question.label}</strong>
          {question.text && <q className="why-question">{question.text}</q>}
        </div>
        <div className="why-nav">
          <button
            type="button"
            className="why-step"
            disabled={!previous}
            onClick={() => previous && onSelect(answerKey(previous))}
            aria-label={previous ? `Previous answer, ${previous.label}` : 'No previous answer'}
          >
            ‹
          </button>
          <button
            type="button"
            className="why-step"
            disabled={!next}
            onClick={() => next && onSelect(answerKey(next))}
            aria-label={next ? `Next answer, ${next.label}` : 'No next answer'}
          >
            ›
          </button>
        </div>
      </div>
      <p className="why-summary" aria-live="polite">
        {summary(question, verdict)}
      </p>
      <div className="why-grid">
        <Panel
          title="What moved the score"
          hint="Each signal group pushes the score up (toward a reaction) or down (toward calm). They start at 0, a typical calm answer, and add up to the final score."
        >
          {verdict ? <Waterfall verdict={verdict} /> : <p className="why-empty">Waiting for the first readings…</p>}
        </Panel>
        <Panel
          title="Compared with calm moments"
          hint="How unusual each group was, next to the same measurement taken while nothing was being asked."
        >
          {final ? <CalmComparison verdict={verdict} /> : <Waiting question={question} />}
        </Panel>
        <Panel
          title="What the sensors did"
          hint="Raw signals from 10 s before the question to 10 s after, with the windows the score is measured over."
          wide
        >
          {final ? <Traces verdict={verdict} /> : <Waiting question={question} />}
        </Panel>
        <Panel
          title="How the answers compare"
          hint="Each answer's deception likelihood side by side, and all of them added up."
        >
          <AnswersChart session={session} selectedLabel={question.label} />
        </Panel>
        <Panel title="Quality checks" hint="A reading is only shown when every check passes.">
          {verdict?.gates.length ? <Checks verdict={verdict} /> : <p className="why-empty">Checked as data comes in.</p>}
        </Panel>
      </div>
      {final && <Numbers verdict={verdict} />}
    </Section>
  )
}
