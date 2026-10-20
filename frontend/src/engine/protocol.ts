/**
 * Message protocol between the page and the Pyodide worker.
 *
 * The worker owns the simulation; the page owns the rendering. Every message
 * is plain JSON so the boundary stays cheap and inspectable.
 */

import type {
  ForkResult,
  GhostAction,
  LandscapeSpec,
  ReplaySession,
  StateFrame,
  TraineeAction,
} from "@/api/types"

/** Where the engine is in its lifecycle. */
export type EngineStatus =
  | "idle"
  | "loading"
  | "ready"
  | "running"
  | "paused"
  | "finished"
  | "error"

/** Page -> worker. */
export type EngineRequest =
  | { type: "init" }
  | { type: "start"; scenario: unknown; sessionId: string }
  | { type: "action"; action: TraineeAction }
  | { type: "finish" }
  | { type: "landscape"; scenario: unknown; requestId: number }
  | {
      type: "fork"
      scenario: unknown
      actions: TraineeAction[]
      ghostActions: GhostAction[]
      request: unknown
      original: unknown
      requestId: number
    }
  | { type: "dispose" }

/** Worker -> page. */
export type EngineResponse =
  | { type: "status"; status: EngineStatus; note?: string; progress?: number }
  | { type: "frame"; frame: StateFrame }
  | { type: "end"; frame: StateFrame; replay: ReplaySession; outcome: unknown }
  | { type: "landscape"; requestId: number; spec: LandscapeSpec }
  | { type: "fork"; requestId: number; result: ForkResult }
  | { type: "error"; message: string }
