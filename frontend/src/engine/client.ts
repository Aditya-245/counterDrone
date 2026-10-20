/**
 * Main-thread API for the client-side simulation engine.
 *
 * One worker serves the whole application: the simulation is a single
 * timeline, so there is one engine and one live session at a time. The page
 * renders frames and sends decisions; the worker owns the simulation.
 */

import type {
  ForkResult,
  GhostAction,
  LandscapeSpec,
  ReplaySession,
  StateFrame,
  TraineeAction,
} from "@/api/types"
import type { EngineRequest, EngineResponse, EngineStatus } from "./protocol"

export type { EngineStatus }

export interface EngineHandlers {
  onFrame?: (frame: StateFrame) => void
  onStatus?: (status: EngineStatus, note?: string) => void
  onEnd?: (frame: StateFrame, replay: ReplaySession, outcome: unknown) => void
  onError?: (message: string) => void
}

/** The handle a page uses to play one session. */
export interface SessionEngine {
  send: (action: TraineeAction) => void
  close: () => void
  readonly status: EngineStatus
  readonly lastFrame: StateFrame | null
  readonly replay: ReplaySession | null
  readonly outcome: unknown
}

interface Pending {
  resolve: (value: never) => void
  reject: (reason: Error) => void
}

class EngineClient {
  private worker: Worker | null = null
  private ready: Promise<void> | null = null
  private status: EngineStatus = "idle"
  private lastFrame: StateFrame | null = null
  private lastReplay: ReplaySession | null = null
  private lastOutcome: unknown = null
  private currentSessionId: string | null = null
  private handlers: EngineHandlers = {}
  private pending = new Map<number, Pending>()
  private requestId = 0

  /** Boot the worker and the Python runtime. Safe to call repeatedly. */
  init(): Promise<void> {
    if (this.ready) return this.ready
    this.ready = new Promise<void>((resolve, reject) => {
      let worker: Worker
      try {
        worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" })
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)))
        return
      }
      this.worker = worker
      let settled = false
      worker.onmessage = (event: MessageEvent<EngineResponse>): void => {
        const message = event.data
        switch (message.type) {
          case "status":
            this.status = message.status
            this.handlers.onStatus?.(message.status, message.note)
            if (message.status === "ready" && !settled) {
              settled = true
              resolve()
            }
            if (message.status === "error" && !settled) {
              settled = true
              reject(new Error(message.note ?? "the simulation engine failed to start"))
            }
            break
          case "frame":
            this.lastFrame = message.frame
            this.handlers.onFrame?.(message.frame)
            break
          case "end":
            this.lastFrame = message.frame
            this.lastReplay = message.replay
            this.lastOutcome = message.outcome
            this.handlers.onEnd?.(message.frame, message.replay, message.outcome)
            break
          case "landscape":
            this.settle(message.requestId, message.spec)
            break
          case "fork":
            this.settle(message.requestId, message.result)
            break
          case "error":
            this.handlers.onError?.(message.message)
            if (!settled) {
              settled = true
              reject(new Error(message.message))
            }
            break
          default:
            break
        }
      }
      worker.onerror = (event: ErrorEvent): void => {
        const error = new Error(event.message || "the simulation worker crashed")
        this.handlers.onError?.(error.message)
        if (!settled) {
          settled = true
          reject(error)
        }
      }
      this.send({ type: "init" })
    })
    return this.ready
  }

  /** The current engine status. */
  getStatus(): EngineStatus {
    return this.status
  }

  /** The most recent frame the engine produced. */
  getLastFrame(): StateFrame | null {
    return this.lastFrame
  }

  /** The after-action-review payload of the session that just finished. */
  getReplay(): ReplaySession | null {
    return this.lastReplay
  }

  /** The outcome summary the counterfactual fork diffs against. */
  getOutcome(): unknown {
    return this.lastOutcome
  }

  /** The session the engine is playing, or the one it last played. */
  getSessionId(): string | null {
    return this.currentSessionId
  }

  /**
   * Start playing a scenario.
   *
   * The engine has to boot before it can play, so this waits for the worker
   * and the Python runtime to be ready. The session id is the one the server
   * minted when the session was registered.
   */
  start(scenario: unknown, sessionId: string, handlers: EngineHandlers = {}): void {
    this.handlers = handlers
    this.lastFrame = null
    this.lastReplay = null
    this.lastOutcome = null
    this.currentSessionId = sessionId
    void this.init()
      .then(() => {
        this.send({ type: "start", scenario, sessionId })
      })
      .catch((error: unknown) => {
        this.handlers.onError?.(error instanceof Error ? error.message : String(error))
      })
  }

  /** Queue one trainee decision. */
  send_action(action: TraineeAction): void {
    if (!this.worker) return
    this.send({ type: "action", action })
  }

  /** End the session early. */
  finish(): void {
    if (!this.worker) return
    this.send({ type: "finish" })
  }

  /** Stop the worker. */
  dispose(): void {
    this.send({ type: "dispose" })
    this.worker?.terminate()
    this.worker = null
    this.ready = null
  }

  /** Generate the world for a scenario, in the browser. */
  async landscape(scenario: unknown): Promise<LandscapeSpec> {
    await this.init()
    return this.request<LandscapeSpec>("landscape", scenario)
  }

  /**
   * Re-simulate from a decision point with a modified action log.
   *
   * The browser supplies the original run and the ghost's decisions it already
   * has, so the fork costs one simulation rather than three.
   */
  async fork(
    scenario: unknown,
    actions: TraineeAction[],
    ghostActions: GhostAction[],
    request: unknown,
    original: unknown,
  ): Promise<ForkResult> {
    await this.init()
    return this.request<ForkResult>("fork", scenario, actions, ghostActions, request, original)
  }

  // ------------------------------------------------------------------ //
  private send(message: EngineRequest): void {
    if (!this.worker) throw new Error("the simulation engine is not started")
    this.worker.postMessage(message)
  }

  private async request<T>(
    kind: "landscape" | "fork",
    ...args: unknown[]
  ): Promise<T> {
    if (!this.worker) throw new Error("the simulation engine is not started")
    const requestId = ++this.requestId
    return new Promise<T>((resolve, reject) => {
      this.pending.set(requestId, {
        resolve: resolve as (value: never) => void,
        reject,
      })
      if (kind === "landscape") {
        this.send({ type: "landscape", scenario: args[0], requestId })
      } else {
        this.send({
          type: "fork",
          scenario: args[0],
          actions: args[1] as TraineeAction[],
          ghostActions: args[2] as GhostAction[],
          request: args[3],
          original: args[4],
          requestId,
        })
      }
    })
  }

  private settle(requestId: number, value: unknown): void {
    const pending = this.pending.get(requestId)
    if (!pending) return
    this.pending.delete(requestId)
    pending.resolve(value as never)
  }
}

/** The one engine the page shares. */
export const engine = new EngineClient()

/**
 * Open a session on the client-side engine.
 *
 * The shape mirrors the socket it replaces: the page sends decisions and
 * receives frames, and the simulation is owned by the engine either way.
 */
export function openSessionEngine(
  scenario: unknown,
  sessionId: string,
  handlers: EngineHandlers = {},
): SessionEngine {
  engine.start(scenario, sessionId, handlers)
  return {
    send: (action: TraineeAction) => engine.send_action(action),
    close: () => engine.finish(),
    get status() {
      return engine.getStatus()
    },
    get lastFrame() {
      return engine.getLastFrame()
    },
    get replay() {
      return engine.getReplay()
    },
    get outcome() {
      return engine.getOutcome()
    },
  }
}
