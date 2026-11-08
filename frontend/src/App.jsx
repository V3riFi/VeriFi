import { useState, useSyncExternalStore } from 'react'
import { IS_DEMO, store } from './lib/store'
import { DEMO_BASELINE_S } from './demo/fakeBackend'
import { SIGNALS, STRESS, alertState, thresholdsFor } from './lib/signals'
import Navbar from './components/Navbar'
import VideoFeed from './components/VideoFeed'
import SessionPanel from './components/SessionPanel'
import SessionHistory from './components/SessionHistory'
import VerdictPanel from './components/VerdictPanel'
import VerdictBreakdown from './components/VerdictBreakdown'
import EvidenceFlow from './components/EvidenceFlow'
import SignalTiles from './components/SignalTiles'
import TimeChart from './components/TimeChart'
import Section from './components/Section'
import { useSectionOpen } from './lib/useSectionOpen'

const SOURCE_NAMES = { gsr: 'the GSR sensor', pulse: 'the pulse sensor', breathing: 'the breathing belt', face: 'face tracking' }

function StressHeadline({ value, baseline, recordingBaseline }) {
  if (recordingBaseline) return <span className="stress-note">Recording baseline…</span>
  if (!baseline || !Number.isFinite(value)) return null
  const over = alertState(value, thresholdsFor(STRESS, baseline)) !== null
  return (
    <span className="stress-headline">
      <strong>{Math.round(value)}</strong>
      <small>/ 100</small>
      {over ? (
        <span className="tile-alert">
          <span aria-hidden="true">▲</span> Over alert line
        </span>
      ) : (
        <span className="tile-note">Within normal range</span>
      )}
    </span>
  )
}

// The closed "Stress & signal charts" dropdown still says where stress is.
function StressStatus({ value, baseline, recordingBaseline, charts }) {
  const extra = charts > 0 ? ` · ${charts} signal chart${charts === 1 ? '' : 's'}` : ''
  if (recordingBaseline) return <span>Recording baseline…{extra}</span>
  if (!baseline || !Number.isFinite(value)) return extra ? <span>{extra.slice(3)}</span> : null
  const over = alertState(value, thresholdsFor(STRESS, baseline)) !== null
  return over ? (
    <span className="tile-alert">
      <span aria-hidden="true">▲</span> {Math.round(value)} / 100, over the alert line{extra}
    </span>
  ) : (
    <span>
      {Math.round(value)} / 100, within normal{extra}
    </span>
  )
}

const CAMERA_STATES = {
  ok: ['good', 'Connected'],
  off: ['muted', 'Off'],
  missing: ['critical', 'No camera'],
  simulated: ['warning', 'Simulated'],
}

function CameraStatus({ status }) {
  const [tone, text] = CAMERA_STATES[status] ?? ['muted', 'Unknown']
  return (
    <span className={`chip chip-${tone}`}>
      <span className="chip-dot" aria-hidden="true" />
      <span className="chip-state">{text}</span>
    </span>
  )
}

export default function App() {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const [openKeys, setOpenKeys] = useState([])
  // Everything but the deception likelihood is a dropdown, closed by default (remembered per browser).
  const [videoOpen, setVideoOpen] = useSectionOpen('video')
  const [sessionsOpen, setSessionsOpen] = useSectionOpen('sessions')
  const [whyOpen, setWhyOpen] = useSectionOpen('why')
  // The data-to-result picture is open by default: it's the overview of how the verdict is built.
  const [flowOpen, setFlowOpen] = useSectionOpen('flow', true)
  const [chartsOpen, setChartsOpen] = useSectionOpen('charts')
  // The answer shown in "Why this reading" (null = the newest).
  const [selectedAnswer, setSelectedAnswer] = useState(null)
  const showAnswer = (key) => {
    setSelectedAnswer(key)
    setWhyOpen(true)
    requestAnimationFrame(() =>
      document.getElementById('verdict-breakdown')?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
    )
  }

  const active = state.sessions.find((s) => s.id === state.activeId) ?? null
  const viewed = state.sessions.find((s) => s.id === state.viewId) ?? null
  const reviewing = viewed !== null && viewed.id !== state.activeId

  // A tile's Show opens its chart, so it also opens the charts dropdown.
  const toggle = (key) => {
    if (!openKeys.includes(key)) setChartsOpen(true)
    setOpenKeys((keys) => (keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]))
  }
  // While a recorded session is on screen, the list stays open so "Back to live" is in reach.
  const reviewingPast = state.viewId !== null && state.activeId === null

  const stressEmpty = !viewed
    ? 'Start a session to record a baseline. The combined stress line appears once it is done.'
    : viewed.baseline
      ? 'No stress values in this session.'
      : 'Recording baseline. The combined stress line starts when it is done.'

  return (
    <div className="app">
      <Navbar connection={state.connection} components={state.components} sampleHz={state.sampleHz} />
      {IS_DEMO && (
        <p className="demo-banner" role="note">
          <strong>Online demo.</strong> Everything here is simulated in your browser by a copy of the backend's fake
          signals: no sensors, no camera, nothing saved. The baseline takes {DEMO_BASELINE_S} seconds (60 on the real
          rig), then a question is asked automatically every 14 to 18 seconds, standing in for the interrogator's
          button.
        </p>
      )}

      <main className="layout">
        <div className="side">
          <Section
            icon="video"
            title="Live video"
            status={<CameraStatus status={state.components?.camera} />}
            open={videoOpen}
            onToggle={setVideoOpen}
          >
            <VideoFeed connection={state.connection} cameraStatus={state.components?.camera} />
          </Section>
          {/* The operator's controls stay open: Ask and Done asking can't wait behind a click. */}
          <SessionPanel state={state} />
          {state.sessions.length > 0 && (
            <Section
              icon="sessions"
              title="Sessions"
              status={`${state.sessions.length} this visit${reviewingPast ? ` · viewing ${viewed?.name ?? ''}` : ''}`}
              open={sessionsOpen || reviewingPast}
              onToggle={setSessionsOpen}
            >
              <SessionHistory sessions={state.sessions} viewId={state.viewId} activeId={state.activeId} />
            </Section>
          )}
        </div>

        <div className="main">
          <VerdictPanel session={viewed} selectedKey={selectedAnswer} onSelect={showAnswer} />
          <EvidenceFlow
            session={viewed}
            selectedKey={selectedAnswer}
            onSelect={showAnswer}
            open={flowOpen}
            onToggle={setFlowOpen}
          />
          <VerdictBreakdown
            session={viewed}
            selectedKey={selectedAnswer}
            onSelect={setSelectedAnswer}
            open={whyOpen}
            onToggle={setWhyOpen}
          />

          <SignalTiles
            latest={state.latest}
            baseline={active?.baseline ?? null}
            components={state.components}
            openKeys={openKeys}
            onToggle={toggle}
            session={viewed}
          />

          <Section
            className="charts"
            icon="charts"
            title="Stress & signal charts"
            status={
              <StressStatus
                value={reviewing ? null : state.latest?.stress}
                baseline={active?.baseline}
                recordingBaseline={active !== null && !active.baseline}
                charts={openKeys.length}
              />
            }
            open={chartsOpen}
            onToggle={setChartsOpen}
          >
            <div className="view-head">
              <h2 className="section-title">
                {reviewing
                  ? `${viewed.name}, recorded`
                  : active
                    ? `${active.name}, live`
                    : 'Live, last 60 seconds'}
              </h2>
              <p className="view-help">
                Shaded band: normal range (baseline ± {active?.baseline?.alertSd ?? viewed?.baseline?.alertSd ?? 2}{' '}
                SD). The line turns red outside it.
              </p>
            </div>

            <TimeChart signal={STRESS} height={260} labelQuestions emptyText={stressEmpty}>
              <StressHeadline
                value={reviewing ? null : state.latest?.stress}
                baseline={active?.baseline}
                recordingBaseline={active !== null && !active.baseline}
              />
            </TimeChart>
            <p className="stress-explainer">
              Every signal's distance from this session's baseline, weighted toward skin conductance and heart rate. A
              signal that is far from its baseline counts for more, so one strong reaction isn't averaged away.
            </p>

            {openKeys.length > 0 ? (
              <div className="signal-charts">
                {SIGNALS.filter((signal) => openKeys.includes(signal.key)).map((signal) => (
                  <TimeChart
                    key={signal.key}
                    signal={signal}
                    emptyText={`No data from ${SOURCE_NAMES[signal.source]}.`}
                  />
                ))}
              </div>
            ) : (
              <p className="view-help">Press Show on any signal above to add its own chart here.</p>
            )}
          </Section>

          <p className="caveat">
            This shows stress and arousal, not lying. An innocent person can react to an accusing question, and a calm
            liar may not react at all. Treat a red section as something to follow up on, never as a verdict.
          </p>
        </div>
      </main>
    </div>
  )
}
