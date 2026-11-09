// "How it adds up": one animated picture of the whole path from data to result.
// Signal groups (left) feed each answer (middle), and every answer feeds the whole-session
// verdict (right). Ribbon widths are the real numbers, not decoration:
//   group -> answer   |weight × z| (the group's contribution; an answer's contributions add up
//                     exactly to its score, backend/verdict.py)
//   answer -> session |log LR| (the answer's evidence; they add up to the session total)
// Warm ribbons push toward a reaction, blue ones toward calm. New answers sweep in, the session
// number counts to its new value, and Replay rebuilds the session one answer at a time.
import { useEffect, useId, useRef, useState } from 'react'
import { answerKey, useAnswerVerdict } from '../lib/useAnswer'
import { INSUFFICIENT, bandInfo, percent, signed } from '../lib/verdict'
import Section from './Section'
import { BandChip } from './VerdictPanel'

// Same groups and order as backend/confidence.py CATEGORIES; used until a verdict names them.
const GROUPS = ['Skin', 'Breathing', 'Cardiac', 'Voice', 'Blink', 'Lip / hand']

const HEAD = 46 // room for the column headings
const FOOT = 10
const PILL_W = 74
const PILL_MIN_H = 24
const GROUP_W = 9
const SESSION_W = 14
const MAX_SCALE = 24 // px per unit of score / evidence, so one answer doesn't fill the panel
const REPLAY_STEP_MS = 1300

/* ---------- small hooks ---------- */

function useWidth(ref) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element) return undefined
    // The content width, without the padding, so the drawing stays inside the box.
    const measure = () => {
      const style = getComputedStyle(element)
      setWidth(Math.max(0, element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)))
    }
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()
    return () => observer.disconnect()
  }, [ref])
  return width
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return reduced
}

const ease = (p) => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2)

function valueAt(anim, now) {
  if (anim.dur <= 0 || now >= anim.t0 + anim.dur) return anim.to
  const p = Math.max(0, (now - anim.t0) / anim.dur)
  return anim.from + (anim.to - anim.from) * ease(p)
}

// Keeps one easing animation per key. sample() is called while rendering on purpose: the
// picture is drawn at "now", and a new target starts from whatever is on screen at that moment.
class Animator {
  anims = new Map()

  sample(targets, durationOf, startOf) {
    const now = performance.now()
    const values = {}
    let moving = false
    for (const [key, to] of Object.entries(targets)) {
      let anim = this.anims.get(key)
      if (!anim) {
        const from = startOf(key, to)
        anim = { from, to, t0: now, dur: from === to ? 0 : durationOf(key) }
        this.anims.set(key, anim)
      } else if (anim.to !== to) {
        anim = { from: valueAt(anim, now), to, t0: now, dur: durationOf(key) }
        this.anims.set(key, anim)
      }
      values[key] = valueAt(anim, now)
      if (anim.dur > 0 && now < anim.t0 + anim.dur) moving = true
    }
    for (const key of [...this.anims.keys()]) if (!(key in targets)) this.anims.delete(key)
    return { values, moving }
  }
}

// Every number in `targets` eases from what is on screen now to its new value; a key seen for
// the first time starts at startOf(key, target). Re-renders every frame while anything moves.
function useTween(targets, durationOf, startOf) {
  const [animator] = useState(() => new Animator())
  const [, setFrame] = useState(0)
  const { values, moving } = animator.sample(targets, durationOf, startOf)
  useEffect(() => {
    if (!moving) return undefined
    const id = requestAnimationFrame(() => setFrame((f) => f + 1))
    return () => cancelAnimationFrame(id)
  })
  return values
}

/* ---------- data ---------- */

// The answers to draw: each one's group contributions (incoming) and evidence (outgoing).
function collect(session, liveQuestion, liveVerdict) {
  const sessionAfter = new Map((session.verdict?.history ?? []).map((h) => [h.label, h.likelihood]))
  const answers = session.questions.map((q) => {
    const live = q === liveQuestion
    const verdict = live ? liveVerdict : q.responded ? q.verdict : null
    const parts = (verdict?.categories ?? [])
      .filter((c) => Number.isFinite(c.contribution) && Math.abs(c.contribution) >= 0.005)
      .map((c) => ({ group: c.name, value: c.contribution }))
    const evidence = !live && Number.isFinite(verdict?.logLr) ? verdict.logLr : null
    return {
      id: answerKey(q),
      label: q.label,
      text: q.text,
      live: live || !q.responded,
      verdict,
      band: verdict?.band ?? null,
      parts,
      evidence,
      sessionAfter: sessionAfter.get(q.label) ?? null,
    }
  })
  const named = session.questions.flatMap((q) => q.verdict?.categories ?? []).map((c) => c.name)
  const groups = [...new Set([...named, ...GROUPS])]
  return { answers, groups }
}

/* ---------- layout ---------- */

function ribbonPath(x0, y0, x1, y1, w) {
  const xm = (x0 + x1) / 2
  return `M${x0},${y0}C${xm},${y0} ${xm},${y1} ${x1},${y1}L${x1},${y1 + w}C${xm},${y1 + w} ${xm},${y0 + w} ${x0},${y0 + w}Z`
}

function centerPath(x0, y0, x1, y1, w) {
  const xm = (x0 + x1) / 2
  const a = y0 + w / 2
  const b = y1 + w / 2
  return `M${x0},${a}C${xm},${a} ${xm},${b} ${x1},${b}`
}

// Target geometry (all y values and heights) for the answers on screen.
function layout(answers, groups, width) {
  const narrow = width < 560
  const labelW = narrow ? 64 : 104
  const rightW = narrow ? 82 : 132
  const xG = labelW
  const xS = width - rightW - SESSION_W
  const xQ = Math.round((xG + GROUP_W + xS) / 2 - PILL_W / 2)

  const inSum = (a) => a.parts.reduce((s, p) => s + Math.abs(p.value), 0)
  const outOf = (a) => (a.evidence === null ? 0 : Math.abs(a.evidence))
  const groupSum = Object.fromEntries(groups.map((g) => [g, 0]))
  for (const a of answers) for (const p of a.parts) groupSum[p.group] += Math.abs(p.value)
  const sumG = Object.values(groupSum).reduce((s, v) => s + v, 0)
  const sumQ = answers.reduce((s, a) => s + Math.max(inSum(a), outOf(a)), 0)
  const sumS = answers.reduce((s, a) => s + outOf(a), 0)

  const gapG = 6
  const gapQ = 8
  const H = Math.max(240, Math.min(560, answers.length * 40))
  const fits = [MAX_SCALE]
  if (sumG > 0) fits.push((H - gapG * (groups.length - 1) - 22 * groups.length) / sumG)
  if (sumQ > 0) fits.push(Math.max(1, H - gapQ * (answers.length - 1)) / sumQ)
  if (sumS > 0) fits.push((H * 0.85) / sumS)
  const k = Math.max(0.5, Math.min(...fits))

  const g = {}
  // Groups column, centered. Each group gets a slot tall enough for its label (and "no data").
  const gh = groups.map((name) => Math.max(4, groupSum[name] * k))
  const slot = groups.map((name, i) => Math.max(gh[i] + 4, groupSum[name] > 0 || narrow ? 22 : 32))
  const colG = slot.reduce((s, h) => s + h, 0) + gapG * (groups.length - 1)
  // Answers column.
  const qh = answers.map((a) => Math.max(PILL_MIN_H, inSum(a) * k + 6, outOf(a) * k + 6))
  const colQ = qh.reduce((s, h) => s + h, 0) + gapQ * Math.max(0, answers.length - 1)
  const hs = Math.max(14, sumS * k)
  const inner = Math.max(colG, colQ, hs, H * 0.6)

  let y = HEAD + (inner - colG) / 2
  groups.forEach((name, i) => {
    g[`g:${name}:y`] = y + (slot[i] - gh[i]) / 2
    g[`g:${name}:h`] = gh[i]
    y += slot[i] + gapG
  })
  y = HEAD + (inner - colQ) / 2
  answers.forEach((a, i) => {
    g[`q:${a.id}:y`] = y
    g[`q:${a.id}:h`] = qh[i]
    y += qh[i] + gapQ
  })
  const sy = HEAD + (inner - hs) / 2
  g['s:y'] = sy
  g['s:h'] = hs

  // Ribbon ends: stacked in answer order at a group, in group order at an answer.
  const groupCursor = Object.fromEntries(groups.map((name) => [name, g[`g:${name}:y`]]))
  let sessionCursor = sy + (hs - sumS * k) / 2
  for (const a of answers) {
    const sorted = [...a.parts].sort((p, q) => groups.indexOf(p.group) - groups.indexOf(q.group))
    let qCursor = g[`q:${a.id}:y`] + (g[`q:${a.id}:h`] - inSum(a) * k) / 2
    for (const p of sorted) {
      const w = Math.abs(p.value) * k
      const id = `r:${p.group}>${a.id}`
      g[`${id}:y0`] = groupCursor[p.group]
      g[`${id}:y1`] = qCursor
      g[`${id}:w`] = w
      groupCursor[p.group] += w
      qCursor += w
    }
    if (a.evidence !== null) {
      const w = Math.abs(a.evidence) * k
      const id = `r:${a.id}>S`
      g[`${id}:y0`] = g[`q:${a.id}:y`] + (g[`q:${a.id}:h`] - w) / 2
      g[`${id}:y1`] = sessionCursor
      g[`${id}:w`] = w
      sessionCursor += w
    }
  }
  return { geometry: g, x: { xG, xQ, xS, labelW, rightW, narrow }, height: HEAD + inner + FOOT }
}

/* ---------- words ---------- */

function describeAnswer(a, groups) {
  if (!a.verdict) return `${a.label}: waiting for the first readings.`
  const parts = [...a.parts]
    .sort((p, q) => groups.indexOf(p.group) - groups.indexOf(q.group))
    .map((p) => `${p.group} ${signed(p.value, 2)}`)
  const score = Number.isFinite(a.verdict.z) ? signed(a.verdict.z, 2) : '–'
  const flow = parts.length ? `${parts.join(', ')} → score ${score}` : 'no signal group scored'
  if (a.live) return `${a.label} (live): ${flow} → ${percent(a.verdict.likelihood)}% so far. It joins the session once it's scored.`
  if (a.evidence === null) return `${a.label}: ${flow}. Not counted because ${a.verdict.reason ?? 'it failed a quality check'}.`
  const info = bandInfo(a.band)
  return (
    `${a.label}: ${flow} → ${percent(a.verdict.likelihood)}% (${info.label}). ` +
    `It adds ${signed(a.evidence, 2)} of evidence ${a.evidence >= 0 ? 'toward a reaction' : 'toward calm'}` +
    (Number.isFinite(a.sessionAfter) ? `, and the session stood at ${percent(a.sessionAfter)}% after it.` : '.') +
    ' Click for details.'
  )
}

function describeGroup(name, answers) {
  const hits = answers.flatMap((a) => a.parts.filter((p) => p.group === name).map((p) => ({ a, p })))
  if (!hits.length) return `${name}: no usable data in these answers, so it adds nothing.`
  const up = hits.filter((h) => h.p.value > 0).map((h) => `${h.a.label} (${signed(h.p.value, 2)})`)
  const down = hits.filter((h) => h.p.value < 0).map((h) => `${h.a.label} (${signed(h.p.value, 2)})`)
  return [
    `${name}:`,
    up.length ? `pushed toward a reaction on ${up.join(', ')}` : null,
    up.length && down.length ? 'and' : null,
    down.length ? `${up.length ? '' : 'pushed '}toward calm on ${down.join(', ')}` : null,
  ]
    .filter(Boolean)
    .join(' ')
}

/* ---------- the picture ---------- */

function Flow({ session, selectedKey, onSelect, replayCount }) {
  const ref = useRef(null)
  const width = useWidth(ref)
  const reduced = useReducedMotion()
  const uid = useId().replace(/:/g, '')
  const [focus, setFocus] = useState(null) // { type: 'group' | 'answer', id }

  const liveQuestion = session.questions.findLast((q) => !q.responded) ?? null
  const liveVerdict = useAnswerVerdict(session, liveQuestion)
  const all = collect(session, liveQuestion, liveVerdict)
  const groups = all.groups
  // While replaying, only the first replayCount answers have arrived.
  const answers = replayCount === null ? all.answers : all.answers.filter((a) => !a.live).slice(0, replayCount)

  const scored = answers.filter((a) => a.evidence !== null)
  const sv = session.verdict
  const target =
    replayCount === null
      ? Number.isFinite(sv?.likelihood) ? sv.likelihood : null
      : (scored.length ? scored[scored.length - 1].sessionAfter : null)
  const sessionBand =
    replayCount === null ? (sv?.band ?? INSUFFICIENT.key) : target === null ? INSUFFICIENT.key : bandOf(target)

  const { geometry, x, height } = layout(answers, groups, Math.max(width, 280))
  const targets = { ...geometry, pct: target ?? 0 }
  for (const a of answers) {
    if (a.parts.length) targets[`in:${a.id}`] = 1
    if (a.evidence !== null) targets[`out:${a.id}`] = 1
  }
  const v = useTween(
    targets,
    (key) => (reduced ? 0 : key.startsWith('in:') ? 900 : key.startsWith('out:') ? 1500 : key === 'pct' ? 1300 : 650),
    (key, to) => (key.startsWith('in:') || key.startsWith('out:') || key === 'pct' ? 0 : to),
  )

  const { xG, xQ, xS, labelW, narrow } = x
  const xQr = xQ + PILL_W
  const inFrac = (a) => Math.min(1, v[`in:${a.id}`] ?? 0)
  // The outgoing ribbon waits for the answer to land before it sweeps on.
  const outFrac = (a) => Math.max(0, Math.min(1, ((v[`out:${a.id}`] ?? 0) - 0.4) / 0.6))

  const related = (kind, ids) => {
    if (!focus) return true
    if (focus.type === 'group') return kind === 'in' ? ids.group === focus.id : false
    return ids.answer === focus.id
  }

  const ribbons = []
  for (const a of answers) {
    for (const p of a.parts) {
      const id = `r:${p.group}>${a.id}`
      const w = v[`${id}:w`]
      if (!Number.isFinite(w)) continue
      ribbons.push({
        id,
        kind: 'in',
        answer: a,
        dir: p.value >= 0 ? 'up' : 'down',
        live: a.live,
        on: related('in', { group: p.group, answer: a.id }),
        d: ribbonPath(xG + GROUP_W, v[`${id}:y0`], xQ, v[`${id}:y1`], w),
        c: centerPath(xG + GROUP_W, v[`${id}:y0`], xQ, v[`${id}:y1`], w),
        w,
      })
    }
    if (a.evidence !== null) {
      const id = `r:${a.id}>S`
      const w = v[`${id}:w`]
      ribbons.push({
        id,
        kind: 'out',
        answer: a,
        dir: a.evidence >= 0 ? 'up' : 'down',
        live: false,
        on: related('out', { answer: a.id }),
        d: ribbonPath(xQr, v[`${id}:y0`], xS, v[`${id}:y1`], w),
        c: centerPath(xQr, v[`${id}:y0`], xS, v[`${id}:y1`], w),
        w,
      })
    }
  }

  // Dots running along the ribbons: the data flowing in. Clipped like their ribbon, so they
  // only appear on the part that has swept in.
  const particles = []
  if (!reduced) {
    for (const r of ribbons) {
      const n = r.w < 2.5 ? 0 : r.w < 10 ? 1 : 2
      for (let i = 0; i < n && particles.length < 90; i++) {
        const dur = r.kind === 'in' ? 2.8 : 2.2
        particles.push({ key: `${r.id}:${i}`, r, dur, begin: -((i / n) * dur + (particles.length % 7) * 0.37) })
      }
    }
  }

  const focusedAnswer = focus?.type === 'answer' ? answers.find((a) => a.id === focus.id) : null
  const focused = focusedAnswer
    ? describeAnswer(focusedAnswer, groups)
    : focus?.type === 'group'
      ? describeGroup(focus.id, answers)
      : null
  const pctText = target === null ? '–' : `${percent(v.pct)}%`
  const info = bandInfo(sessionBand)

  return (
    <div className="flow" ref={ref}>
      {width > 0 && (
        <svg
          className="flow-svg"
          width={width}
          height={height}
          role="img"
          aria-label={`Signal groups feed each answer, and the answers add up to the whole session: ${pctText}, ${info.label}.`}
          onMouseLeave={() => setFocus(null)}
        >
          <defs>
            {answers.map((a) => (
              <g key={a.id}>
                <clipPath id={`${uid}-in-${cssId(a.id)}`}>
                  <rect x={xG} y={0} width={(xQ - xG) * inFrac(a) + 1} height={height} />
                </clipPath>
                <clipPath id={`${uid}-out-${cssId(a.id)}`}>
                  <rect x={xQr} y={0} width={(xS - xQr) * outFrac(a) + 1} height={height} />
                </clipPath>
              </g>
            ))}
          </defs>

          {/* Column headings: the steps from data to result. */}
          <g className="flow-heads" aria-hidden="true">
            <text x={0} y={16} className="flow-step">
              1 · Signals
            </text>
            <text x={0} y={31} className="flow-sub">
              {narrow ? 'vs. calm' : 'change from calm, weighted'}
            </text>
            <text x={xQ + PILL_W / 2} y={16} textAnchor="middle" className="flow-step">
              2 · Each answer
            </text>
            <text x={xQ + PILL_W / 2} y={31} textAnchor="middle" className="flow-sub">
              {narrow ? 'score' : 'signals summed into a score'}
            </text>
            <text x={xS} y={16} className="flow-step">
              {narrow ? '3 · Session' : '3 · Whole session'}
            </text>
            <text x={xS} y={31} className="flow-sub">
              {narrow ? 'combined' : 'all answers combined'}
            </text>
          </g>

          {/* Ribbons */}
          <g>
            {ribbons.map((r) => (
              <path
                key={r.id}
                d={r.d}
                clipPath={`url(#${uid}-${r.kind}-${cssId(r.answer.id)})`}
                className={`flow-ribbon flow-${r.dir}${r.live ? ' flow-live' : ''}${r.on ? '' : ' flow-dim'}${
                  focus && r.on ? ' flow-hot' : ''
                }`}
              />
            ))}
          </g>

          {/* Particles */}
          <g aria-hidden="true">
            {particles.map((p) => (
              <g key={p.key} clipPath={`url(#${uid}-${p.r.kind}-${cssId(p.r.answer.id)})`}>
                <circle r={p.r.w > 14 ? 2.2 : 1.7} className={`flow-dot flow-dot-${p.r.dir}${p.r.on ? '' : ' flow-dim'}`}>
                  <animateMotion dur={`${p.dur}s`} begin={`${p.begin.toFixed(2)}s`} repeatCount="indefinite" path={p.r.c} />
                </circle>
              </g>
            ))}
          </g>

          {/* Signal groups */}
          {groups.map((name) => {
            const y = v[`g:${name}:y`]
            const h = v[`g:${name}:h`]
            const used = answers.some((a) => a.parts.some((p) => p.group === name))
            const on = !focus || (focus.type === 'group' && focus.id === name) || focus.type === 'answer'
            return (
              <g
                key={name}
                className={`flow-group${used ? '' : ' flow-unused'}${on ? '' : ' flow-dim'}`}
                tabIndex={0}
                role="button"
                aria-label={describeGroup(name, answers)}
                onMouseEnter={() => setFocus({ type: 'group', id: name })}
                onFocus={() => setFocus({ type: 'group', id: name })}
                onBlur={() => setFocus(null)}
              >
                <rect x={0} y={y - 4} width={xG + GROUP_W + 4} height={h + 8} className="flow-hit" />
                <rect x={xG} y={y} width={GROUP_W} height={h} rx={2} className="flow-group-bar" />
                <text x={xG - 8} y={y + h / 2 + 4} textAnchor="end" className="flow-label">
                  {narrow && name === 'Lip / hand' ? 'Lip/hand' : name}
                </text>
                {!used && !narrow && (
                  <text x={xG - 8} y={y + h / 2 + 15} textAnchor="end" className="flow-sub">
                    no data
                  </text>
                )}
              </g>
            )
          })}

          {/* Answers */}
          {answers.map((a) => {
            const y = v[`q:${a.id}:y`]
            const h = v[`q:${a.id}:h`]
            const band = a.band ?? INSUFFICIENT.key
            const on = !focus || (focus.type === 'answer' && focus.id === a.id) || (focus.type === 'group' && a.parts.some((p) => p.group === focus.id))
            const value = a.verdict ? `${percent(a.verdict.likelihood)}%` : '…'
            return (
              <g
                key={`${a.id}:${a.live ? 'live' : band}`}
                className={`flow-answer${a.live ? ' flow-answer-live' : ' flow-pop'}${on ? '' : ' flow-dim'}${
                  selectedKey === a.id ? ' flow-selected' : ''
                }`}
                tabIndex={0}
                role="button"
                aria-label={describeAnswer(a, groups)}
                style={{ transformOrigin: `${xQ + PILL_W / 2}px ${y + h / 2}px` }}
                onMouseEnter={() => setFocus({ type: 'answer', id: a.id })}
                onFocus={() => setFocus({ type: 'answer', id: a.id })}
                onBlur={() => setFocus(null)}
                onClick={() => onSelect(a.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelect(a.id)
                  }
                }}
              >
                <rect
                  x={xQ}
                  y={y}
                  width={PILL_W}
                  height={h}
                  rx={Math.min(12, h / 2)}
                  className={a.live ? 'flow-pill-live' : `flow-pill band-fill-${band}`}
                />
                <text x={xQ + PILL_W / 2} y={y + h / 2 + 4} textAnchor="middle" className={`flow-pill-text flow-text-${a.live ? 'live' : band}`}>
                  {a.label} · {a.live ? (a.verdict ? value : 'live') : a.evidence === null ? '–' : value}
                </text>
              </g>
            )
          })}

          {/* Whole session */}
          <g className="flow-session">
            <rect
              x={xS}
              y={v['s:y']}
              width={SESSION_W}
              height={v['s:h']}
              rx={3}
              className={`flow-session-bar band-fill-${sessionBand}`}
            />
            <g key={`${replayCount ?? 'all'}:${scored.length}`} className={reduced ? '' : 'flow-glow'}>
              <text x={xS + SESSION_W + 10} y={v['s:y'] + v['s:h'] / 2 - 2} className={`flow-total${narrow ? ' flow-total-narrow' : ''}`}>
                {pctText}
              </text>
              <text x={xS + SESSION_W + 11} y={v['s:y'] + v['s:h'] / 2 + 16} className={`flow-total-band flow-band-${sessionBand}`}>
                {target === null ? 'No reading yet' : `${info.glyph} ${info.label}`}
              </text>
              <text x={xS + SESSION_W + 11} y={v['s:y'] + v['s:h'] / 2 + 31} className="flow-sub">
                {narrow ? `${scored.length} counted` : `${scored.length} answer${scored.length === 1 ? '' : 's'} counted`}
              </text>
            </g>
          </g>
        </svg>
      )}
      <p className="flow-readout" aria-live="polite">
        {focused ??
          (answers.length
            ? 'Hover over a signal or an answer to follow its ribbons. The wider the ribbon, the more it moved the result.'
            : 'Replaying…')}
      </p>
      <p className="flow-key" style={{ paddingLeft: narrow ? 0 : Math.max(0, labelW - 60) }}>
        <span className="flow-swatch flow-up" aria-hidden="true" /> toward a reaction
        <span className="flow-swatch flow-down" aria-hidden="true" /> toward calm
        <span className="flow-swatch flow-swatch-live" aria-hidden="true" /> answer still coming in
      </p>
    </div>
  )
}

const cssId = (id) => id.replace(/[^a-zA-Z0-9_-]/g, '_')

// Same cutoffs as lib/verdict.js BANDS (for the session value partway through a replay).
function bandOf(likelihood) {
  if (likelihood >= 0.85) return 'high'
  if (likelihood >= 0.6) return 'elevated'
  if (likelihood >= 0.3) return 'unclear'
  return 'low'
}

/* ---------- the section ---------- */

export default function EvidenceFlow({ session, selectedKey, onSelect, open, onToggle }) {
  const [replayCount, setReplayCount] = useState(null)
  const finished = session?.questions.filter((q) => q.responded).length ?? 0

  // Replay: one answer arrives every REPLAY_STEP_MS until all of them are back, then the
  // picture goes back to live after the last one has landed.
  useEffect(() => {
    if (replayCount === null) return undefined
    const done = replayCount >= finished
    const id = setTimeout(
      () => setReplayCount((c) => (c === null || done ? null : c + 1)),
      done ? REPLAY_STEP_MS * 2 : replayCount === 0 ? 350 : REPLAY_STEP_MS,
    )
    return () => clearTimeout(id)
  }, [replayCount, finished])

  if (!session?.baseline || !session.questions.length) return null
  const sv = session.verdict
  const status = (
    <>
      <span>
        {session.questions.length} answer{session.questions.length === 1 ? '' : 's'} →{' '}
        {Number.isFinite(sv?.likelihood) ? `${percent(sv.likelihood)}%` : 'no reading yet'}
      </span>
      {sv && Number.isFinite(sv.likelihood) && <BandChip band={sv.band} />}
      <span className="section-status-hint">from data to result</span>
    </>
  )
  return (
    <Section id="evidence-flow" className="flow-section" icon="flow" title="How it adds up" status={status} open={open} onToggle={onToggle}>
      <div className="flow-head">
        <p className="flow-intro">
          The signal groups feed into each answer's score, and every answer feeds into the verdict for the whole
          session. The width of each ribbon shows the real number behind it.
        </p>
        <button
          type="button"
          className="flow-replay"
          disabled={finished === 0}
          onClick={() => setReplayCount(replayCount === null ? 0 : null)}
        >
          {replayCount === null ? '▶ Replay session' : '■ Stop replay'}
        </button>
      </div>
      <Flow
        session={session}
        selectedKey={selectedKey}
        onSelect={onSelect}
        replayCount={replayCount}
      />
    </Section>
  )
}
