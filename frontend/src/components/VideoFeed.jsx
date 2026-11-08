import { useEffect, useRef, useState } from 'react'
import { IS_DEMO, store } from '../lib/store'

const STALE_AFTER_MS = 2000

function placeholderText(connection, cameraStatus) {
  if (IS_DEMO) return 'No video in the online demo. Run VeriFi on the laptop to see the webcam.'
  if (connection !== 'open') return 'Backend offline'
  if (cameraStatus === 'off') return 'Video is turned off on the backend (VERIFI_CAMERA=off)'
  if (cameraStatus === 'missing') return 'No camera found by the backend'
  return 'Waiting for video…'
}

export default function VideoFeed({ connection, cameraStatus }) {
  const imageRef = useRef(null)
  const lastFrameAt = useRef(0)
  const [hasVideo, setHasVideo] = useState(false)

  useEffect(() => {
    // Frames bypass React state: setting src directly avoids 10 re-renders a second.
    const unsubscribe = store.onFrame((frame) => {
      imageRef.current.src = `data:image/jpeg;base64,${frame.jpeg}`
      lastFrameAt.current = Date.now()
      setHasVideo(true)
    })
    const timer = setInterval(() => {
      if (Date.now() - lastFrameAt.current > STALE_AFTER_MS) setHasVideo(false)
    }, 500)
    return () => {
      unsubscribe()
      clearInterval(timer)
    }
  }, [])

  // The frame only: the "Live video" dropdown around it (App.jsx) carries the title.
  return (
    <div className="video-frame">
      <img ref={imageRef} alt="Live camera view of the subject" hidden={!hasVideo} />
      {hasVideo && (
        <span className="live-tag video-live">
          <span className="live-dot" aria-hidden="true" /> Live
        </span>
      )}
      {!hasVideo && <p className="video-placeholder">{placeholderText(connection, cameraStatus)}</p>}
    </div>
  )
}
