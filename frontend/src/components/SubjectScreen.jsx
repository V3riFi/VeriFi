export default function SubjectScreen({ session }) {
  const { status, trials } = session
  const last = trials[trials.length - 1]
  const showing = last && last.end === null ? last : null

  return (
    <main className="subject">
      {status === 'waiting' ? (
        <p className="subject-prompt">Answer “no” to every item.</p>
      ) : showing ? (
        <p className="subject-item">{showing.item}</p>
      ) : (
        // A fixation point between items, so nothing on screen competes for attention.
        <p className="subject-fixation" aria-label="Next item coming">
          +
        </p>
      )}
      <p className="subject-hint">Esc: operator view</p>
    </main>
  )
}
