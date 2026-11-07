import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import { LIVE_WINDOW_S, store } from '../lib/store'

const CHART_HEIGHT = 168
const SYNC_KEY = 'verifi-signals'

// minSpan stops the y axis zooming into sensor noise on a quiet signal.
const SIGNALS = [
  { field: 'gsr', title: 'Skin conductance', unit: 'raw', minSpan: 40 },
  { field: 'hr', title: 'Heart rate', unit: 'bpm', minSpan: 16 },
  { field: 'flex', title: 'Breathing belt', unit: 'raw', minSpan: 80 },
]

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function readColors() {
  return {
    line: cssVar('--accent'),
    grid: cssVar('--grid'),
    axis: cssVar('--text-muted'),
    label: cssVar('--text-secondary'),
    band: cssVar('--band'),
    bandPick: cssVar('--band-pick'),
  }
}

let colors = null
const getColors = () => (colors ??= readColors())

function formatClock(seconds) {
  const whole = Math.round(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function reviewStep(span) {
  return [5, 10, 15, 30, 60, 120, 300, 600].find((step) => span / step <= 7) ?? 1200
}

function drawBands(u, trials, pick, withLabels) {
  const { left, top, width, height } = u.bbox
  const { ctx } = u
  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top, width, height)
  ctx.clip()
  for (const trial of trials) {
    const x0 = u.valToPos(trial.start, 'x', true)
    const x1 = u.valToPos(trial.end ?? u.scales.x.max, 'x', true)
    const isPick = trial.item === pick
    if (withLabels) {
      const fontPx = Math.round(11 * uPlot.pxRatio)
      ctx.font = `${isPick ? 600 : 400} ${fontPx}px system-ui, sans-serif`
      ctx.textBaseline = 'top'
      ctx.textAlign = 'left' // uPlot leaves this set by its axis drawing
      ctx.fillStyle = getColors().label
      // Skip a label that wouldn't fit inside its band.
      if (ctx.measureText(trial.item).width + 8 * uPlot.pxRatio <= x1 - x0) {
        ctx.fillText(trial.item, x0 + 4 * uPlot.pxRatio, top + 4 * uPlot.pxRatio)
      }
    } else {
      ctx.fillStyle = isPick ? getColors().bandPick : getColors().band
      ctx.fillRect(x0, top, x1 - x0, height)
    }
  }
  ctx.restore()
}

function buildOptions({ signal, review, width, trialsRef, pickRef, labelBands, readoutRef }) {
  const formatX = (u, t) =>
    review ? formatClock(t - u.scales.x.min) : `${Math.round(u.scales.x.max - t)}s ago`

  return {
    width,
    height: CHART_HEIGHT,
    padding: [10, 12, 0, 0],
    legend: { show: false },
    cursor: {
      y: false,
      drag: { x: false, y: false },
      sync: { key: SYNC_KEY },
      points: { size: 8 },
    },
    scales: {
      x: {
        time: false,
        range: (u, min, max) => {
          if (min == null) return [0, 1]
          return review ? [min, Math.max(max, min + 1)] : [max - LIVE_WINDOW_S, max]
        },
      },
      y: {
        range: (u, min, max) => {
          if (min == null) return [0, 1]
          const span = Math.max(max - min, signal.minSpan)
          const mid = (min + max) / 2
          // Snap to a coarse step so axis labels don't jitter with every sample.
          const step = signal.minSpan / 4
          return [
            Math.floor((mid - span * 0.6) / step) * step,
            Math.ceil((mid + span * 0.6) / step) * step,
          ]
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
          if (review) {
            const step = reviewStep(max - min)
            const splits = []
            for (let t = min; t <= max; t += step) splits.push(t)
            return splits
          }
          return [60, 45, 30, 15, 0].map((ago) => max - ago)
        },
        values: (u, splits) =>
          splits.map((t) => {
            if (review) return formatClock(t - u.scales.x.min)
            const ago = Math.round(u.scales.x.max - t)
            return ago === 0 ? 'now' : `${ago}s ago`
          }),
      },
      {
        stroke: () => getColors().axis,
        grid: { stroke: () => getColors().grid, width: 1 },
        ticks: { show: false },
        font: '11px system-ui, sans-serif',
        size: 44,
      },
    ],
    series: [
      {},
      {
        label: signal.title,
        stroke: () => getColors().line,
        width: 2,
        points: { show: false },
        spanGaps: false,
      },
    ],
    hooks: {
      drawClear: [(u) => drawBands(u, trialsRef.current, pickRef.current, false)],
      draw: [
        (u) => {
          if (labelBands) drawBands(u, trialsRef.current, pickRef.current, true)
        },
      ],
      setCursor: [
        (u) => {
          const idx = u.cursor.idx
          const value = idx == null ? null : u.data[1][idx]
          readoutRef.current.textContent =
            value == null ? '' : `${value.toFixed(1)} ${signal.unit} · ${formatX(u, u.data[0][idx])}`
        },
      ],
    },
  }
}

function SignalChart({ signal, review, session, labelBands }) {
  const containerRef = useRef(null)
  const readoutRef = useRef(null)
  const emptyRef = useRef(null)
  // Refs so the chart's draw hooks see new trials without rebuilding the chart.
  const trialsRef = useRef(session.trials)
  const pickRef = useRef(session.result?.pick ?? null)

  useEffect(() => {
    trialsRef.current = session.trials
    pickRef.current = session.result?.pick ?? null
  }, [session])

  useEffect(() => {
    const container = containerRef.current
    const options = buildOptions({
      signal,
      review,
      width: container.clientWidth,
      trialsRef,
      pickRef,
      labelBands,
      readoutRef,
    })
    const u = new uPlot(options, [[], []], container)

    const pushData = () => {
      const buffer = review ? store.getRecord() : store.getLive()
      if (!buffer) return
      u.setData([buffer.t, buffer[signal.field]])
      emptyRef.current.hidden = buffer.t.length > 0
    }
    pushData()

    // Live charts redraw once a frame, and only if something changed.
    let frame = null
    let seenVersion = store.getDataVersion()
    if (!review) {
      const tick = () => {
        const version = store.getDataVersion()
        if (version !== seenVersion) {
          seenVersion = version
          pushData()
        }
        frame = requestAnimationFrame(tick)
      }
      frame = requestAnimationFrame(tick)
    }

    const resize = new ResizeObserver(() => {
      if (container.clientWidth !== u.width) {
        u.setSize({ width: container.clientWidth, height: CHART_HEIGHT })
      }
    })
    resize.observe(container)

    const scheme = window.matchMedia('(prefers-color-scheme: dark)')
    const onScheme = () => {
      colors = null
      u.setData(u.data) // rebuilds cached stroke colors, then redraws
    }
    scheme.addEventListener('change', onScheme)

    return () => {
      cancelAnimationFrame(frame)
      resize.disconnect()
      scheme.removeEventListener('change', onScheme)
      u.destroy()
    }
  }, [signal, review, labelBands])

  return (
    <figure className="chart-card">
      <figcaption>
        <span className="chart-title">
          {signal.title} <small>{signal.unit}</small>
        </span>
        <span className="chart-readout" ref={readoutRef} aria-live="off" />
      </figcaption>
      <div className="chart-plot">
        <div ref={containerRef} />
        <div className="chart-empty" ref={emptyRef}>
          {review ? 'No samples were recorded in this session.' : 'Waiting for samples from the backend…'}
        </div>
      </div>
    </figure>
  )
}

export default function SignalCharts({ review, session }) {
  return SIGNALS.map((signal, index) => (
    <SignalChart key={signal.field} signal={signal} review={review} session={session} labelBands={index === 0} />
  ))
}
