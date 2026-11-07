const TILES = [
  { field: 'gsr', label: 'Skin conductance', unit: 'raw' },
  { field: 'hr', label: 'Heart rate', unit: 'bpm' },
  { field: 'breath_rate', label: 'Breathing', unit: 'per min' },
  { field: 'blink_rate', label: 'Blinks', unit: 'per min' },
]

export default function StatTiles({ latest }) {
  return (
    <dl className="tiles">
      {TILES.map(({ field, label, unit }) => {
        const value = latest?.[field]
        return (
          <div className="tile" key={field}>
            <dt>{label}</dt>
            <dd>
              {Number.isFinite(value) ? Math.round(value) : '–'} <small>{unit}</small>
            </dd>
          </div>
        )
      })}
    </dl>
  )
}
