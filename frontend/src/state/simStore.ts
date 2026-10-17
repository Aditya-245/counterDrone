/**
 * Global client state: the live frame, UI selection, trainee identity and theme.
 *
 * Deliberately small - server state arrives over REST/WebSocket and is cached by
 * TanStack Query; Zustand only holds what the HUD needs at 60 fps.
 */

import { create } from "zustand"
import type { StateFrame, TrackView } from "@/api/types"

export type Theme = "dark" | "light"

export interface SimState {
  sessionId: string | null
  scenarioId: string | null
  traineeId: string
  frame: StateFrame | null
  previousFrame: StateFrame | null
  tracks: TrackView[]
  selectedTrackId: string | null
  sensorAzimuth: Record<string, number>
  status: "idle" | "connecting" | "open" | "closed" | "error"
  connectionNote: string
  theme: Theme
  paused: boolean

  setFrame: (frame: StateFrame) => void
  setStatus: (status: SimState["status"], note?: string) => void
  select: (trackId: string | null) => void
  steerSensor: (sensorId: string, azimuth: number) => void
  setTrainee: (traineeId: string) => void
  setSession: (sessionId: string | null, scenarioId: string | null) => void
  setTheme: (theme: Theme) => void
  setPaused: (paused: boolean) => void
  reset: () => void
}

const EMPTY_FRAME: StateFrame | null = null

function readStoredTrainee(): string {
  try {
    return window.localStorage.getItem("skygym.trainee") ?? "usr_alvarez"
  } catch {
    return "usr_alvarez"
  }
}

function readStoredTheme(): Theme {
  try {
    return (window.localStorage.getItem("skygym.theme") as Theme) ?? "dark"
  } catch {
    return "dark"
  }
}

export const useSimStore = create<SimState>((set) => ({
  sessionId: null,
  scenarioId: null,
  traineeId: readStoredTrainee(),
  frame: EMPTY_FRAME,
  previousFrame: EMPTY_FRAME,
  tracks: [],
  selectedTrackId: null,
  sensorAzimuth: {},
  status: "idle",
  connectionNote: "",
  theme: readStoredTheme(),
  paused: false,

  setFrame: (frame) =>
    set((s) => ({
      frame,
      previousFrame: s.frame,
      tracks: frame.tracks,
    })),
  setStatus: (status, note = "") => set({ status, connectionNote: note }),
  select: (trackId) => set({ selectedTrackId: trackId }),
  steerSensor: (sensorId, azimuth) =>
    set((s) => ({ sensorAzimuth: { ...s.sensorAzimuth, [sensorId]: azimuth } })),
  setTrainee: (traineeId) => {
    try {
      window.localStorage.setItem("skygym.trainee", traineeId)
    } catch {
      /* storage may be unavailable; the in-memory value still works */
    }
    set({ traineeId })
  },
  setSession: (sessionId, scenarioId) => set({ sessionId, scenarioId }),
  setTheme: (theme) => {
    try {
      window.localStorage.setItem("skygym.theme", theme)
    } catch {
      /* ignore */
    }
    if (typeof document !== "undefined") {
      document.documentElement.dataset["theme"] = theme
    }
    set({ theme })
  },
  setPaused: (paused) => set({ paused }),
  reset: () =>
    set({
      frame: EMPTY_FRAME,
      previousFrame: EMPTY_FRAME,
      tracks: [],
      selectedTrackId: null,
      paused: false,
    }),
}))

export function selectedTrack(state: SimState): TrackView | null {
  if (!state.selectedTrackId) return null
  return state.tracks.find((t) => t.id === state.selectedTrackId) ?? null
}
