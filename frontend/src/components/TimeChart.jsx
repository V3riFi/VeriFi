import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import { LIVE_WINDOW_S, store } from '../lib/store'
import { alertState, thresholdsFor } from '../lib/signals'

const SYNC_KEY = 'verifi-signals'
const MIN_SESSION_SPAN_S = 60

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

let colors = null
function getColors() {
  colors ??= {
    line: cssVar('--accent'),
    alert: cssVar('--alert'),
    grid: cssVar('--grid'),
    axis: cssVar('--text-muted'),
    label: cssVar('--text-secondary'),
    band: cssVar('--band'),
    normal: cssVar('--band-normal'),
    marker: cssVar('--text-secondary'),
  }
  return colors
}

function formatClock(seconds) {
  const whole = Math.max(0, Math.round(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function niceStep(span) {
  return [5, 10, 15, 30, 60, 120, 300, 600].find((step) => span / step <= 7) ?? 1200
}

// Blue inside the normal range, red beyond it: a vertical gradient with hard
// stops at the threshold lines, so the line changes colour exactly where it crosses.
function lineStroke(u, thresholds) {
  const { line, alert } = getColors()
  if (!thresholds || u.scales.y.min == null) return line
  const { top, height } = u.bbox
  const fraction = (value) => Math.min(1, Math.max(0, (u.valToPos(value, 'y', true) - top) / height))
  const gradient = u.ctx.createLinearGradient(0, top, 0, top + height)
  if (thresholds.upper !== null) {
    const upper = fraction(thresholds.upper)
    gradient.addColorStop(0, alert)
    gradient.addColorStop(upper, alert)
    gradient.addColorStop(upper, line)
  } else {
    gradient.addColorStop(0, line)
  }
  if (thresholds.lower !== null) {
    const lower = fraction(thresholds.lower)
    gradient.addColorStop(lower, line)
    gradient.addColorStop(lower, alert)
    gradient.addColorStop(1, alert)
  } else {
    gradient.addColorStop(1, line)
  }
  return gradient
}

function smallText(ctx, weight = 400) {
  ctx.font = `${weight} ${Math.round(11 * uPlot.pxRatio)}px system-ui, sans-serif`
  ctx.textAlign = 'left' // uPlot leaves this set by its axis drawing
  ctx.textBaseline = 'top'
}

// Beneath the line: the baseline period, the normal range and the baseline level.
function drawUnderlay(u, view) {
  const { session, thresholds, isStress } = view
  const { ctx } = u
  const { left, top, width, height } = u.bbox
  const px = uPlot.pxRatio
  const c = getColors()
  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top, width, height)
  ctx.clip()

  if (session) {
    const x0 = u.valToPos(session.startT, 'x', true)
    const end = session.baseline ? session.baselineEndT : Math.min(session.baselineEndT, u.scales.x.max)
    const x1 = u.valToPos(end, 'x', true)
    ctx.fillStyle = c.band
    ctx.fillRect(x0, top, x1 - x0, height)
    smallText(ctx)
    ctx.fillStyle = c.label
    if (ctx.measureText('Baseline').width + 8 * px < x1 - x0) ctx.fillText('Baseline', x0 + 4 * px, top + 4 * px)

    if (session.voice) {
      const v0 = u.valToPos(session.voice.startT, 'x', true)
      const vEnd = session.voice.done ? session.voice.endT : Math.min(session.voice.endT, u.scales.x.max)
      const v1 = u.valToPos(vEnd, 'x', true)
      ctx.fillStyle = c.band
      ctx.fillRect(v0, top, v1 - v0, height)
      ctx.fillStyle = c.label
      const text = 'Voice baseline'
      if (ctx.measureText(text).width + 8 * px < v1 - v0) ctx.fillText(text, v0 + 4 * px, top + 4 * px)
    }
  }

  if (thresholds) {
    const xStart = Math.max(left, u.valToPos(session.baselineEndT, 'x', true))
    const yTop = u.valToPos(thresholds.rangeHigh, 'y', true)
    const yBottom = u.valToPos(thresholds.rangeLow, 'y', true)
    ctx.fillStyle = c.normal
    ctx.fillRect(xStart, yTop, left + width - xStart, yBottom - yTop)

    // Baseline level, plus the alert line on the stress chart.
    const lines = isStress
      ? [{ value: thresholds.upper, text: 'alert', color: c.alert }]
      : [{ value: thresholds.mean, text: 'baseline', color: c.axis }]
    for (const { value, text, color } of lines) {
      const y = Math.round(u.valToPos(value, 'y', true)) + 0.5
      ctx.strokeStyle = color
      ctx.lineWidth = px
      ctx.beginPath()
      ctx.moveTo(xStart, y)
      ctx.lineTo(left + width, y)
      ctx.stroke()
      smallText(ctx)
      ctx.fillStyle = c.label
      ctx.textAlign = 'right'
      ctx.textBaseline = 'bottom'
      ctx.fillText(text, left + width - 4 * px, y - 2 * px)
    }
  }
  ctx.restore()
}

// Above the line: a marker where each question started.
function drawQuestions(u, view, withLabels) {
  const questions = view.session?.questions ?? []
  if (!questions.length) return
  const { ctx } = u
  const { left, top, width, height } = u.bbox
  const px = uPlot.pxRatio
  const c = getColors()
  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top, width, height)
  ctx.clip()
  const rowEnds = [] // right edge of the last label in each row, so close questions stack
  for (const q of questions) {
    const x = Math.round(u.valToPos(q.t, 'x', true)) + 0.5
    ctx.strokeStyle = c.marker
    ctx.lineWidth = px
    ctx.beginPath()
    ctx.moveTo(x, top)
    ctx.lineTo(x, top + height)
    ctx.stroke()
    if (!withLabels) continue
    smallText(ctx, 600)
    const text = Number.isFinite(q.spike) ? `${q.label} ${q.spike.toFixed(1)} SD` : q.label
    const textX = x + 4 * px
    let row = rowEnds.findIndex((end) => textX > end)
    if (row === -1) row = rowEnds.length
    if (row > 3) continue // too crowded: the marker line still shows
    rowEnds[row] = textX + ctx.measureText(text).width + 8 * px
    ctx.fillStyle = c.label
    ctx.fillText(text, textX, top + (18 + row * 14) * px)
  }
  ctx.restore()
}

function buildOptions({ signal, width, height, viewRef, readoutRef, labelQuestions }) {
  const isSession = () => viewRef.current.session !== null
  const formatX = (u, t) =>
    isSession() ? formatClock(t - viewRef.current.session.startT) : `${Math.round(u.scales.x.max - t)}s ago`

  const formatY = (value) => {
    if (signal.boolean) return value >= 0.5 ? 'Yes' : 'No'
    return Math.abs(value) < 10 ? value.toFixed(2) : value.toFixed(1)
  }

  return {
    width,
    height,
    padding: [10, 12, 0, 0],
    legend: { show: false },
    cursor: { y: false, drag: { x: false, y: false }, sync: { key: SYNC_KEY }, points: { size: 8 } },
    scales: {
      x: {
        time: false,
        // Uses the real last timestamp, not uPlot's min/max: with a single
        // point uPlot pads around the value itself, which for Unix time spans decades.
        range: (u) => {
          const times = u.data[0]
          const last = times.length ? times[times.length - 1] : null
          const { session } = viewRef.current
          if (session) {
            const end = session.endT ?? Math.max(last ?? session.startT, session.startT + MIN_SESSION_SPAN_S)
            return [session.startT, Math.max(end, session.startT + 1)]
          }
          if (last === null) return [0, LIVE_WINDOW_S]
          return [last - LIVE_WINDOW_S, last]
        },
      },
      y: {
        range: (u, min, max) => {
          if (signal.key === 'stress') return [0, 100]
          if (signal.boolean) return [-0.15, 1.15]
          const { thresholds } = viewRef.current
          if (min == null) {
            if (!thresholds) return [0, 1]
            min = max = thresholds.mean
          }
          // Keep the whole normal range in view so the baseline is always visible.
          if (thresholds) {
            min = Math.min(min, thresholds.rangeLow)
            max = Math.max(max, thresholds.rangeHigh)
          }
          const span = Math.max(max - min, signal.minSpan)
          const mid = (min + max) / 2
          const step = signal.minSpan / 4 // snap so the labels don't jitter with every sample
          return [Math.floor((mid - span * 0.6) / step) * step, Math.ceil((mid + span * 0.6) / step) * step]
        },
      },
    },
    axes: [
      {
        stroke: () => getColors().axis,
        grid: { stroke: () => getColors().grid, width: 1 },
        ticks: { show: false },
        font: '11px system-ui, sans-serif',
        size: 28,
        splits: (u, _axis, min, max) => {
          if (!isSession()) return [60, 45, 30, 15, 0].map((ago) => max - ago)
          const step = niceStep(max - min)
          const splits = []
          for (let t = min; t <= max && splits.length < 50; t += step) splits.push(t)
          return splits
        },
        values: (u, splits) =>
          splits.map((t) => {
            if (isSession()) return formatClock(t - viewRef.current.session.startT)
            const ago = Math.round(u.scales.x.max - t)
            return ago === 0 ? 'now' : `${ago}s ago`
          }),
      },
      {
        stroke: () => getColors().axis,
        grid: { stroke: () => getColors().grid, width: 1 },
        ticks: { show: false },
        font: '11px system-ui, sans-serif',
        size: 48,
        ...(signal.boolean && { splits: () => [0, 1], values: (u, splits) => splits.map(formatY) }),
      },
    ],
    series: [
      {},
      {
        label: signal.label,
        stroke: (u) => lineStroke(u, viewRef.current.thresholds),
        width: signal.key === 'stress' ? 2.5 : 2,
        points: { show: false },
        ...(signal.boolean && { paths: uPlot.paths.stepped({ align: 1 }) }),
      },
    ],
    hooks: {
      drawClear: [(u) => drawUnderlay(u, viewRef.current)],
      draw: [(u) => drawQuestions(u, viewRef.current, labelQuestions)],
      setCursor: [
        (u) => {
          const idx = u.cursor.idx
          const value = idx == null ? null : u.data[1][idx]
          if (value == null) {
            readoutRef.current.textContent = ''
            return
          }
          const state = alertState(value, viewRef.current.thresholds)
          const flag = state ? ` · ${state} normal` : ''
          readoutRef.current.textContent = `${formatY(value)} ${signal.unit} · ${formatX(u, u.data[0][idx])}${flag}`
        },
      ],
    },
  }
}

export default function TimeChart({ signal, height = 168, labelQuestions = false, emptyText, children }) {
  const containerRef = useRef(null)
  const readoutRef = useRef(null)
  const emptyRef = useRef(null)
  const viewRef = useRef({ session: null, thresholds: null, isStress: signal.key === 'stress' })

  useEffect(() => {
    const container = containerRef.current
    const u = new uPlot(
      buildOptions({ signal, width: container.clientWidth, height, viewRef, readoutRef, labelQuestions }),
      [[], []],
      container,
    )

    let seenVersion = -1
    let frame = null
    const tick = () => {
      const version = store.getDataVersion()
      if (version !== seenVersion) {
        seenVersion = version
        const { buffer, session } = store.getChartSource()
        viewRef.current = {
          session,
          thresholds: thresholdsFor(signal, session?.baseline),
          isStress: signal.key === 'stress',
        }
        const values = buffer[signal.field]
        u.setData([buffer.t, values])
        emptyRef.current.hidden = values.findLastIndex((v) => v !== null) !== -1
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)

    const resize = new ResizeObserver(() => {
      if (container.clientWidth !== u.width) u.setSize({ width: container.clientWidth, height })
    })
    resize.observe(container)

    const scheme = window.matchMedia('(prefers-color-scheme: dark)')
    const onScheme = () => {
      colors = null
      u.redraw(false)
    }
    scheme.addEventListener('change', onScheme)
    const themeObserver = new MutationObserver(onScheme)
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })

    return () => {
      themeObserver.disconnect()
      cancelAnimationFrame(frame)
      resize.disconnect()
      scheme.removeEventListener('change', onScheme)
      u.destroy()
    }
  }, [signal, height, labelQuestions])

  return (
    <figure className="chart-card">
      <figcaption>
        <span className="chart-title">
          {signal.label} {signal.unit && <small>{signal.unit}</small>}
        </span>
        <span className="chart-readout" ref={readoutRef} />
        {children}
      </figcaption>
      <div className="chart-plot" style={{ minHeight: height }}>
        <div ref={containerRef} />
        <div className="chart-empty" ref={emptyRef}>
          <span>{emptyText}</span>
        </div>
      </div>
    </figure>
  )
}
