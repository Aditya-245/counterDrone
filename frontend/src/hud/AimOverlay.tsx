/**
 * Aim-assist reticle and the contact class chooser.
 *
 * The trainee moves the reticle over a drone; when it locks, Enter opens the
 * chooser and 1-5 pick a class. The reticle is positioned straight from the
 * shared aim state inside a rAF loop, so it follows the target at 60 fps
 * without re-rendering the page.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import type { TraineeCall, TrackView } from "@/api/types"
import { aim } from "@/scene/aim"

/** The five calls the trainee can make, in key order. */
const CHOICES: { call: TraineeCall; key: string }[] = [
  { call: "hostile", key: "1" },
  { call: "civil", key: "2" },
  { call: "friendly", key: "3" },
  { call: "bird", key: "4" },
  { call: "unknown", key: "5" },
]

/** Approximate chooser box, used to keep the popup inside the scene. */
const CHOOSER_SIZE = { w: 188, h: 200 }

export default function AimOverlay({
  tracks,
  chooserRequest = 0,
  onLock,
  onClassify,
}: {
  tracks: TrackView[]
  /** Bumped by the page when the trainee double-clicks in the scene. */
  chooserRequest?: number
  onLock: (trackId: string | null) => void
  onClassify: (trackId: string, call: TraineeCall) => void
}): JSX.Element {
  const host = useRef<HTMLDivElement | null>(null)
  const reticle = useRef<HTMLDivElement | null>(null)
  const [visible, setVisible] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const [chooserFor, setChooserFor] = useState<string | null>(null)
  const [chooserAt, setChooserAt] = useState({ x: 0, y: 0 })

  // Publish the modal flag so the page keymap stands down while it is open.
  useEffect(() => {
    aim.chooserOpen = chooserFor !== null
  }, [chooserFor])

  useEffect(() => {
    return () => {
      aim.chooserOpen = false
    }
  }, [])

  // Follow the locked contact at frame rate. `setHovered` with an unchanged
  // value is a no-op for React, so this stays cheap.
  useEffect(() => {
    let raf = 0
    const tick = (): void => {
      const el = reticle.current
      const box = host.current
      if (el && box) {
        const x = ((aim.drawn.nx + 1) / 2) * box.clientWidth
        const y = ((1 - aim.drawn.ny) / 2) * box.clientHeight
        el.style.transform = `translate3d(${x}px, ${y}px, 0)`
      }
      setVisible(aim.active)
      setHovered(aim.active ? aim.lockedId : null)
      raf = window.requestAnimationFrame(tick)
    }
    raf = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(raf)
  }, [])

  // Report the lock upward so the readout panel follows the reticle.
  useEffect(() => {
    onLock(hovered)
  }, [hovered, onLock])

  const openChooser = useCallback((): void => {
    const box = host.current
    if (!box || !hovered) return
    const x = ((aim.drawn.nx + 1) / 2) * box.clientWidth
    const y = ((1 - aim.drawn.ny) / 2) * box.clientHeight
    // Keep the popup inside the scene: to the right of the reticle when there
    // is room, otherwise flush against the edge.
    const maxX = Math.max(8, box.clientWidth - CHOOSER_SIZE.w - 8)
    const maxY = Math.max(8, box.clientHeight - CHOOSER_SIZE.h - 8)
    setChooserAt({
      x: Math.min(Math.max(8, x + 12), maxX),
      y: Math.min(Math.max(8, y - CHOOSER_SIZE.h / 2), maxY),
    })
    setChooserFor(hovered)
  }, [hovered])

  // The latest `openChooser` without making it a dependency: a new request must
  // not re-open the popup when the pointer merely moves to another contact.
  const openLatest = useRef(openChooser)
  openLatest.current = openChooser
  useEffect(() => {
    if (chooserRequest > 0) openLatest.current()
  }, [chooserRequest])

  const choose = useCallback(
    (call: TraineeCall): void => {
      if (!chooserFor) return
      onClassify(chooserFor, call)
      setChooserFor(null)
    },
    [chooserFor, onClassify],
  )

  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return
      if (e.key === "Escape" && chooserFor) {
        e.preventDefault()
        setChooserFor(null)
        return
      }
      if (e.key === "Enter") {
        if (!chooserFor) {
          e.preventDefault()
          openChooser()
        }
        return
      }
      if (!chooserFor) return
      const choice = CHOICES.find((c) => c.key === e.key)
      if (choice) {
        e.preventDefault()
        choose(choice.call)
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [chooserFor, openChooser, choose])

  const track = tracks.find((t) => t.id === chooserFor) ?? null

  return (
    <div className="aim-layer" ref={host} data-testid="aim-layer">
      <div
        className={`reticle${visible ? " on" : ""}${hovered ? " locked" : ""}`}
        ref={reticle}
        data-testid="reticle"
        aria-hidden="true"
      >
        <span className="reticle-ring" />
        <span className="reticle-cross" />
      </div>

      {hovered && !chooserFor && (
        <div className="aim-hint" style={{ right: 12, bottom: 12 }}>
          <span className="kbd">Enter</span> identify <strong>{hovered}</strong>
        </div>
      )}

      {chooserFor && (
        <div
          className="class-chooser"
          role="dialog"
          aria-label={`Identify ${chooserFor}`}
          style={{ left: chooserAt.x, top: chooserAt.y }}
          data-testid="class-chooser"
        >
          <div className="chooser-head">
            <strong>{chooserFor}</strong>
            <span className="faint">
              {track?.suggested_class ?? "unclassified"} ·{" "}
              {track?.range_m != null ? `${Math.round(track.range_m)} m` : "—"}
            </span>
          </div>
          {CHOICES.map((c) => (
            <button
              key={c.call}
              type="button"
              className="chooser-row"
              onClick={() => choose(c.call)}
            >
              <span className="kbd">{c.key}</span>
              {c.call}
            </button>
          ))}
          <div className="chooser-foot">
            <span className="kbd">Esc</span> cancel
          </div>
        </div>
      )}
    </div>
  )
}
