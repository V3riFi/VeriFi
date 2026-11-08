import { useSyncExternalStore } from 'react'
import { store } from './store'

// Identifies one answer within a session (labels can repeat if the operator types them).
export const answerKey = (question) => `${question.label}-${question.t}`

// An answer's final verdict once scored, otherwise the live estimate on its partial window.
// Only the calling component re-renders when a live estimate arrives.
export function useAnswerVerdict(session, question) {
  const live = useSyncExternalStore(store.subscribeProgress, () =>
    question && !question.responded ? store.getProgress(session.id, question.label) : null,
  )
  if (!question) return null
  return question.verdict ?? live?.verdict ?? null
}
