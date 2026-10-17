/**
 * Track table: sortable, filterable, keyboard accessible, with a fixed row
 * height so the table never reflows as tracks update.
 */

import { useMemo, useState } from "react"
import type { TrackView } from "@/api/types"
import { formatBearing, formatRange } from "@/api/format"
import { LabelBadge } from "@/components/ui"

type SortKey = "id" | "range" | "bearing" | "tti" | "quality" | "alt" | "speed"
type FilterKey = "all" | "unacknowledged" | "lowconfidence" | "mine"

export interface TrackTableProps {
  tracks: TrackView[]
  selectedId: string | null
  onSelect: (id: string) => void
}

function tti(trk: TrackView): number {
  return trk.time_to_perimeter_s ?? Number.POSITIVE_INFINITY
}

export default function TrackTable({ tracks, selectedId, onSelect }: TrackTableProps): JSX.Element {
  const [sort, setSort] = useState<SortKey>("tti")
  const [ascending, setAscending] = useState(true)
  const [filter, setFilter] = useState<FilterKey>("all")
  const [query, setQuery] = useState("")

  const rows = useMemo(() => {
    let list = tracks
    if (filter === "unacknowledged") list = list.filter((t) => !t.acknowledged)
    if (filter === "lowconfidence") list = list.filter((t) => t.class_confidence < 0.5)
    if (filter === "mine") list = list.filter((t) => t.acknowledged)
    if (query.trim().length > 0) {
      const q = query.trim().toLowerCase()
      list = list.filter(
        (t) =>
          t.id.toLowerCase().includes(q) ||
          (t.suggested_class ?? "").includes(q) ||
          (t.trainee_class ?? "").includes(q),
      )
    }
    const dir = ascending ? 1 : -1
    return [...list].sort((a, b) => {
      switch (sort) {
        case "id":
          return dir * a.id.localeCompare(b.id)
        case "range":
          return dir * (a.range_m - b.range_m)
        case "bearing":
          return dir * (a.bearing_deg - b.bearing_deg)
        case "tti":
          return dir * (tti(a) - tti(b))
        case "quality":
          return dir * (a.quality - b.quality)
        case "alt":
          return dir * (a.altitude_m - b.altitude_m)
        case "speed":
          return dir * (a.speed_mps - b.speed_mps)
        default:
          return 0
      }
    })
  }, [tracks, filter, query, sort, ascending])

  const header = (key: SortKey, label: string): JSX.Element => (
    <th
      onClick={() => {
        if (sort === key) setAscending((v) => !v)
        else {
          setSort(key)
          setAscending(true)
        }
      }}
      aria-sort={sort === key ? (ascending ? "ascending" : "descending") : "none"}
      scope="col"
    >
      {label}
      {sort === key ? (ascending ? " ▲" : " ▼") : ""}
    </th>
  )

  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <input
          aria-label="Filter tracks"
          placeholder="filter tracks"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ width: 160 }}
        />
        {(["all", "unacknowledged", "mine", "lowconfidence"] as FilterKey[]).map((f) => (
          <button
            key={f}
            type="button"
            className="btn"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            style={
              filter === f ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined
            }
          >
            {f}
          </button>
        ))}
        <span className="spacer" />
        <span className="faint mono">
          {rows.length}/{tracks.length} tracks
        </span>
      </div>

      <div className="scroll" style={{ maxHeight: 190 }}>
        <table data-testid="track-table">
          <thead>
            <tr>
              {header("id", "Track")}
              <th scope="col">Class hint</th>
              <th scope="col">Conf</th>
              <th scope="col">Your call</th>
              {header("range", "Range")}
              {header("bearing", "Bearing")}
              {header("alt", "Alt")}
              {header("speed", "Speed")}
              {header("tti", "TTI")}
              {header("quality", "Q")}
              <th scope="col">Sensors</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((trk) => (
              <tr
                key={trk.id}
                className={trk.id === selectedId ? "selected" : undefined}
                onClick={() => onSelect(trk.id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    onSelect(trk.id)
                  }
                }}
              >
                <td className="mono">{trk.id}</td>
                <td className="muted mono">{(trk.suggested_class ?? "—").replace(/_/g, " ")}</td>
                <td className="mono">{(trk.class_confidence * 100).toFixed(0)}%</td>
                <td>
                  <LabelBadge label={trk.trainee_class} />
                </td>
                <td className="mono">{formatRange(trk.range_m)}</td>
                <td className="mono">{formatBearing(trk.bearing_deg)}</td>
                <td className="mono">{trk.altitude_m.toFixed(0)} m</td>
                <td className="mono">{trk.speed_mps.toFixed(0)} m/s</td>
                <td className="mono">
                  {trk.time_to_perimeter_s === null
                    ? "—"
                    : `${trk.time_to_perimeter_s.toFixed(0)}s`}
                </td>
                <td className="mono">{(trk.quality * 100).toFixed(0)}</td>
                <td className="faint mono">{trk.sensors.join(",") || "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={11} className="faint">
                  No tracks.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
