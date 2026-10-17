import type { ReactNode } from "react"

export function Panel({
  title,
  actions,
  children,
  className = "",
}: {
  title?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}): JSX.Element {
  return (
    <section className={`panel ${className}`.trim()}>
      {(title || actions) && (
        <div className="panel-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="spacer" />}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

export function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: ReactNode
  tone?: "ok" | "warn" | "crit"
}): JSX.Element {
  const colour =
    tone === "ok"
      ? "var(--ok)"
      : tone === "warn"
        ? "var(--warn)"
        : tone === "crit"
          ? "var(--crit)"
          : undefined
  return (
    <div className="stat">
      <span className="value" style={{ color: colour }}>
        {value}
      </span>
      <span className="label">{label}</span>
    </div>
  )
}

export function LabelBadge({ label }: { label: string | null }): JSX.Element {
  const tone = label ?? "unknown"
  return <span className={`badge ${tone}`}>{(label ?? "unknown").toUpperCase()}</span>
}

export function Spinner({ label = "Loading" }: { label?: string }): JSX.Element {
  return (
    <div className="muted" role="status" aria-live="polite">
      {label}…
    </div>
  )
}

export function ErrorNote({ message }: { message: string }): JSX.Element {
  return (
    <div className="error" role="alert">
      {message}
    </div>
  )
}
