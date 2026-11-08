// Small line icons for section titles: inline SVG in the text colour (works in light and dark),
// decorative only (the title next to it carries the meaning).
const PATHS = {
  video: (
    <>
      <rect x="2" y="6" width="14" height="12" rx="2" />
      <path d="M16 10.5 22 7v10l-6-3.5z" />
    </>
  ),
  sessions: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </>
  ),
  why: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20.5 20.5-4.5-4.5" />
    </>
  ),
  flow: (
    <>
      <path d="M2 5h3c5 0 7 7 12 7h5M2 19h3c5 0 7-7 12-7" />
      <path d="M2 12h15" />
    </>
  ),
  body: <path d="M2 12h4.5l2.5-6 4.5 12 2.5-6H22" />,
  face: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  voice: (
    <>
      <rect x="9" y="2.5" width="6" height="12" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3.5" />
    </>
  ),
  charts: (
    <>
      <path d="M3 3v18h18" />
      <path d="m7 15 4-5 3 3 5-7" />
    </>
  ),
}

export default function Icon({ name }) {
  if (!PATHS[name]) return null
  return (
    <svg
      className="section-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  )
}
