import { useCallback, useState } from 'react'

const storageKey = (name) => `verifi.section.${name}`

// Whether a dropdown section is open: closed unless `initial`, then remembered in this browser
// so a reload keeps the operator's layout. Only a convenience: storage may be empty or blocked.
export function readSectionOpen(name, initial = false) {
  try {
    const value = window.localStorage.getItem(storageKey(name))
    return value === null ? initial : value === 'open'
  } catch {
    return initial
  }
}

export function writeSectionOpen(name, open) {
  try {
    window.localStorage.setItem(storageKey(name), open ? 'open' : 'closed')
  } catch {
    // Private mode or blocked storage: the section just won't be remembered.
  }
}

export function useSectionOpen(name, initial = false) {
  const [open, setOpenState] = useState(() => readSectionOpen(name, initial))
  const setOpen = useCallback(
    (value) => {
      setOpenState(value)
      writeSectionOpen(name, value)
    },
    [name],
  )
  return [open, setOpen]
}
