/**
 * Camera view requests.
 *
 * The scene lives inside the Canvas and the buttons live in the DOM overlay, so a
 * preset click travels through this tiny mutable channel - the same pattern the
 * aim overlay uses for the reticle. React state would re-render the page on every
 * click for no benefit.
 */

export type ViewPreset = "ground" | "site" | "top"

export interface ViewRequestState {
  /** The preset being moved to, or null when the view is free. */
  preset: ViewPreset | null
  /** Bumped on every request so repeated clicks on the same preset still act. */
  nonce: number
}

export const viewRequest: ViewRequestState = { preset: null, nonce: 0 }

/** Ask the camera to move to `preset`. */
export function requestView(preset: ViewPreset): void {
  viewRequest.preset = preset
  viewRequest.nonce += 1
}
