import { useEffect, useState } from 'react'

// The current time in seconds (Unix, like the backend's timestamps), refreshed every `everyMs`.
// For countdowns and clocks; only the component using it re-renders.
export function useNow(everyMs = 500) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), everyMs)
    return () => clearInterval(timer)
  }, [everyMs])
  return now
}
