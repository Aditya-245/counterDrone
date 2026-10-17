/**
 * Screen-space aim state, shared between the 3D scene and the DOM crosshair.
 *
 * The scene projects every track to normalised device coordinates once per
 * rendered frame; the crosshair overlay reads that back at the same rate and
 * moves itself. Both sides run at 60 fps, so this is a plain mutable singleton
 * rather than React state - re-rendering the page per frame would defeat the
 * point of the instanced scene.
 */

/** A track centre in normalised device coordinates (-1..1, y up). */
export interface ProjectedTrack {
  id: string
  nx: number
  ny: number
}

export interface AimState {
  /** Track centres, refreshed every rendered frame by the scene. */
  projected: ProjectedTrack[]
  /** Where the pointer is, in NDC. */
  pointer: { nx: number; ny: number }
  /** Where the reticle is drawn, in NDC (snapped, or the pointer). */
  drawn: { nx: number; ny: number }
  /** Track the reticle is locked onto, if any. */
  lockedId: string | null
  /** False until the pointer is over the scene. */
  active: boolean
  /** True while the class chooser is open; the page keymap stands down. */
  chooserOpen: boolean
}

/** NDC radius within which the reticle snaps onto a contact. */
export const AIM_SNAP_RADIUS = 0.09

export const aim: AimState = {
  projected: [],
  pointer: { nx: 0, ny: 0 },
  drawn: { nx: 0, ny: 0 },
  lockedId: null,
  active: false,
  chooserOpen: false,
}

export function resetAim(): void {
  aim.projected = []
  aim.lockedId = null
  aim.active = false
  aim.chooserOpen = false
}

/**
 * Choose the contact the reticle should lock onto, and where to draw it.
 *
 * Aim assist: the nearest projected contact inside `AIM_SNAP_RADIUS` wins, so
 * the trainee only has to get *near* a drone. Ties fall to the first in the
 * projected list, which is the server's track order and therefore stable.
 */
export function snapAim(): void {
  let lockedId: string | null = null
  let bestDistance = AIM_SNAP_RADIUS
  let nx = aim.pointer.nx
  let ny = aim.pointer.ny

  for (const candidate of aim.projected) {
    const distance = Math.hypot(candidate.nx - aim.pointer.nx, candidate.ny - aim.pointer.ny)
    if (distance < bestDistance) {
      bestDistance = distance
      lockedId = candidate.id
      nx = candidate.nx
      ny = candidate.ny
    }
  }

  aim.lockedId = lockedId
  aim.drawn.nx = nx
  aim.drawn.ny = ny
}
