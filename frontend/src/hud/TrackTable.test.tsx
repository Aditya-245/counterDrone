import { describe, expect, it } from "vitest"
import { render, screen, fireEvent, within } from "@testing-library/react"
import TrackTable from "@/hud/TrackTable"
import type { TrackView } from "@/api/types"

function track(over: Partial<TrackView> = {}): TrackView {
  return {
    id: "T0001",
    x: 1000,
    y: 500,
    z: 120,
    vx: -10,
    vy: 0,
    vz: 0,
    position_sigma_m: 18,
    velocity_sigma_mps: 2,
    quality: 0.7,
    age_s: 12,
    sensors: ["radar1"],
    suggested_class: "fpv_fast",
    suggested_label: "hostile",
    class_confidence: 0.8,
    range_m: 1118,
    bearing_deg: 26.5,
    altitude_m: 120,
    speed_mps: 10,
    radial_mps: -10,
    time_to_perimeter_s: 96,
    acknowledged: false,
    trainee_class: null,
    trainee_priority: null,
    neutralised: false,
    missed: false,
    confirmed: true,
    ...over,
  }
}

const TRACKS: TrackView[] = [
  track({ id: "T0001", range_m: 3000, bearing_deg: 10, time_to_perimeter_s: 200 }),
  track({
    id: "T0002",
    range_m: 400,
    bearing_deg: 200,
    time_to_perimeter_s: 12,
    acknowledged: true,
    trainee_class: "hostile",
  }),
  track({
    id: "T0003",
    range_m: 1800,
    bearing_deg: 90,
    class_confidence: 0.2,
    suggested_label: "civil",
  }),
]

describe("TrackTable", () => {
  it("renders one row per track with its identifying fields", () => {
    render(<TrackTable tracks={TRACKS} selectedId={null} onSelect={() => {}} />)
    const table = screen.getByTestId("track-table")
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows).toHaveLength(3)
    expect(screen.getByText("T0001")).toBeInTheDocument()
    expect(screen.getByText("T0002")).toBeInTheDocument()
  })

  it("sorts by time-to-impact ascending by default", () => {
    render(<TrackTable tracks={TRACKS} selectedId={null} onSelect={() => {}} />)
    const rows = within(screen.getByTestId("track-table")).getAllByRole("row").slice(1)
    expect(within(rows[0] as HTMLElement).getByText("T0002")).toBeInTheDocument()
  })

  it("re-sorts when a header is clicked and toggles direction", () => {
    render(<TrackTable tracks={TRACKS} selectedId={null} onSelect={() => {}} />)
    const rangeHeader = screen.getByRole("columnheader", { name: /Range/i })
    fireEvent.click(rangeHeader)
    let rows = within(screen.getByTestId("track-table")).getAllByRole("row").slice(1)
    expect(within(rows[0] as HTMLElement).getByText("T0002")).toBeInTheDocument()
    fireEvent.click(rangeHeader)
    rows = within(screen.getByTestId("track-table")).getAllByRole("row").slice(1)
    expect(within(rows[0] as HTMLElement).getByText("T0001")).toBeInTheDocument()
  })

  it("reports the selected track on click and on Enter", () => {
    const picked: string[] = []
    render(<TrackTable tracks={TRACKS} selectedId={null} onSelect={(id) => picked.push(id)} />)
    const table = screen.getByTestId("track-table")
    const row = within(table).getAllByRole("row")[1] as HTMLElement
    const cell = within(row).getAllByRole("cell")[0] as HTMLElement
    // The table sorts by time-to-impact, so the first row is the most urgent.
    expect(cell.textContent).toBe("T0002")
    fireEvent.click(cell)
    expect(picked).toEqual(["T0002"])

    fireEvent.keyDown(row, { key: "Enter" })
    expect(picked).toEqual(["T0002", "T0002"])
  })

  it("filters to unacknowledged tracks", () => {
    render(<TrackTable tracks={TRACKS} selectedId={null} onSelect={() => {}} />)
    fireEvent.click(screen.getByRole("button", { name: "unacknowledged" }))
    const rows = within(screen.getByTestId("track-table")).getAllByRole("row").slice(1)
    expect(rows).toHaveLength(2)
    expect(screen.getByText("2/3 tracks")).toBeInTheDocument()
  })

  it("shows the trainee call next to the machine hint", () => {
    render(<TrackTable tracks={TRACKS} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText("HOSTILE")).toBeInTheDocument()
    // T0001 and T0003 have no trainee call yet, so both read UNKNOWN.
    expect(screen.getAllByText("UNKNOWN")).toHaveLength(2)
  })
})
