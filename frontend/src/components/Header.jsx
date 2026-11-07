const LABELS = {
  open: 'Connected',
  connecting: 'Connecting to backend…',
  closed: 'Backend offline, retrying…',
}

export default function Header({ connection, sampleHz }) {
  return (
    <header className="header">
      <div className="brand">
        <h1>VeriFi</h1>
        <span>Concealed Information Test</span>
      </div>
      <div className={`status status-${connection === 'open' ? 'good' : 'warning'}`} role="status">
        <span className="status-dot" aria-hidden="true" />
        {LABELS[connection]}
        {connection === 'open' && <span className="status-detail">{sampleHz} samples/s</span>}
      </div>
    </header>
  )
}
