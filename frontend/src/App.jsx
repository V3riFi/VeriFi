import { useState, useSyncExternalStore } from 'react'
import { IS_DEMO, store } from './lib/store'
import { DEMO_BASELINE_S } from './demo/fakeBackend'
import { SIGNALS, STRESS, alertState, thresholdsFor } from './lib/signals'
import Navbar from './components/Navbar'
import VideoFeed from './components/VideoFeed'
import SessionPanel from './components/SessionPanel'
import SessionHistory from './components/SessionHistory'
import SignalTiles from './components/SignalTiles'
import TimeChart from './components/TimeChart'

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

export default function App() {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const [openKeys, setOpenKeys] = useState([])

  const active = state.sessions.find((s) => s.id === state.activeId) ?? null
  const viewed = state.sessions.find((s) => s.id === state.viewId) ?? null
  const reviewing = viewed !== null && viewed.id !== state.activeId

  const toggle = (key) =>
    setOpenKeys((keys) => (keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]))

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
          rig), then a question is asked automatically every 10 to 16 seconds, standing in for the interrogator's
          button.
        </p>
      )}

      <main className="layout">
        <div className="side">
          <VideoFeed connection={state.connection} cameraStatus={state.components?.camera} />
          <SessionPanel state={state} />
          <SessionHistory sessions={state.sessions} viewId={state.viewId} activeId={state.activeId} />
        </div>

        <div className="main">
          <SignalTiles
            latest={state.latest}
            baseline={active?.baseline ?? null}
            components={state.components}
            openKeys={openKeys}
            onToggle={toggle}
          />

          <div className="view-head">
            <h2 className="section-title">
              {reviewing
                ? `${viewed.name}, recorded`
                : active
                  ? `${active.name}, live`
                  : 'Live, last 60 seconds'}
            </h2>
            <p className="view-help">
              Shaded band: normal range (baseline ± {active?.baseline?.alertSd ?? viewed?.baseline?.alertSd ?? 2} SD).
              The line turns red outside it.
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

          {openKeys.length > 0 && (
            <div className="signal-charts">
              {SIGNALS.filter((signal) => openKeys.includes(signal.key)).map((signal) => (
                <TimeChart
                  key={signal.key}
                  signal={signal}
                  emptyText={`No data from ${SOURCE_NAMES[signal.source]}.`}
                />
              ))}
            </div>
          )}

          <p className="caveat">
            This shows stress and arousal, not lying. An innocent person can react to an accusing question, and a calm
            liar may not react at all. Treat a red section as something to follow up on, never as a verdict.
          </p>
        </div>
      </main>
    </div>
  )
}
