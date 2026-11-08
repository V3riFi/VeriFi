import Icon from './Icon'

// A dropdown section: its icon, title and a one-line status while closed, its content when open.
// Closed content isn't rendered at all, so hidden charts and video cost nothing.
export default function Section({ id, icon, title, status, open, onToggle, className = '', children }) {
  return (
    <details
      id={id}
      className={`section${className ? ` ${className}` : ''}`}
      open={open}
      onToggle={(event) => {
        const now = event.currentTarget.open
        if (now !== open) onToggle(now)
      }}
    >
      <summary>
        {icon && <Icon name={icon} />}
        <span className="section-name">{title}</span>
        {status && <span className="section-status">{status}</span>}
      </summary>
      {open && <div className="section-body">{children}</div>}
    </details>
  )
}
