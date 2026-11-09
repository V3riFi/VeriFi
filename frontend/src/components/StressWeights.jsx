import { SIGNALS } from '../lib/signals'

const OTHER_LABELS = { breath_hold: 'Breath holds' }
const labelOf = (field) => OTHER_LABELS[field] ?? SIGNALS.find((s) => s.field === field)?.label ?? field

// How much each signal counts toward the combined stress score. The weights come from the
// backend (scoring.STRESS_WEIGHTS), so this can't drift from what is actually calculated.
export default function StressWeights({ weights }) {
  const entries = Object.entries(weights ?? {}).sort((a, b) => b[1] - a[1])
  const total = entries.reduce((sum, [, weight]) => sum + weight, 0)
  if (!total) return null

  return (
    <div className="stress-weights">
      <h3 className="stress-weights-title">What makes up this score</h3>
      <ul>
        {entries.map(([field, weight]) => {
          const share = Math.round((weight / total) * 100)
          return (
            <li key={field}>
              <span className="stress-weights-name">{labelOf(field)}</span>
              <span className="stress-weights-track" aria-hidden="true">
                <span className="stress-weights-fill" style={{ width: `${share}%` }} />
              </span>
              <span className="stress-weights-share">{share}%</span>
            </li>
          )
        })}
      </ul>
      <p>
        Starting weights. A signal more than 1 SD from its baseline counts for more, so its share rises during a
        reaction. Skin conductance is simulated and has no weight. Breathing rate is left out because talking changes it,
        so only breath holds count, and voice is shown separately.
      </p>
    </div>
  )
}
