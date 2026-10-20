/// <reference lib="webworker" />
/**
 * The SKYGYM simulation worker.
 *
 * The browser runs the simulation, not the server. This worker boots Pyodide,
 * loads the *same* `app.core` source the server runs, and drives the
 * deterministic engine at the canonical 10 Hz.
 *
 * Because the engine is pure, the state hash a session produces here is
 * bit-identical to the one the server reproduces from the same action log -
 * which is what makes the server-side verification still meaningful.
 */

import type { PyodideInterface } from "pyodide"
import type { ForkResult, LandscapeSpec, ReplaySession, StateFrame } from "@/api/types"
import type { EngineRequest, EngineResponse, EngineStatus } from "./protocol"

const TICK_MS = 100

/** Where the vendored Pyodide runtime and the Python sources are served from. */
const PYODIDE_INDEX = "/pyodide/"
const PYAPP_MANIFEST = "/pyapp/manifest.json"
const PYAPP_ROOT = "/pyapp"

/** The Python entry points the worker drives. Each returns a JSON string. */
interface EngineApi {
  skygym_start: (scenarioJson: string, sessionId: string) => unknown
  skygym_tick: () => unknown
  skygym_action: (actionJson: string) => unknown
  skygym_finish: () => unknown
  skygym_landscape: (scenarioJson: string) => unknown
  skygym_fork: (
    scenarioJson: string,
    actionsJson: string,
    ghostJson: string,
    requestJson: string,
    originalJson: string,
  ) => unknown
}

let pyodide: PyodideInterface | null = null
let engine: EngineApi | null = null

let timer: ReturnType<typeof setTimeout> | null = null
let nextTickAt = 0
let running = false
let finished = false

/** The boot promise, so a message that arrives early waits for the engine. */
let booting: Promise<void> | null = null

function post(message: EngineResponse): void {
  ;(self as unknown as Worker).postMessage(message)
}

function setStatus(status: EngineStatus, note?: string, progress?: number): void {
  post({ type: "status", status, note, progress })
}

/**
 * Write the Python sources into Pyodide's filesystem.
 *
 * The manifest maps a path relative to `/pyapp` to its source text. Writing
 * them into the virtual filesystem is what lets `import app.core.engine` work
 * exactly as it does on the server.
 */
async function installPythonSources(): Promise<void> {
  if (!pyodide) throw new Error("pyodide is not loaded")
  const response = await fetch(PYAPP_MANIFEST)
  if (!response.ok) {
    throw new Error(`cannot read the python manifest (HTTP ${response.status})`)
  }
  const manifest = (await response.json()) as Record<string, string>
  const fs = pyodide.FS
  fs.mkdirTree(PYAPP_ROOT)
  for (const [path, source] of Object.entries(manifest)) {
    const parts = path.split("/")
    const dir = parts.slice(0, -1).join("/")
    if (dir) fs.mkdirTree(`${PYAPP_ROOT}/${dir}`)
    fs.writeFile(`${PYAPP_ROOT}/${path}`, source)
  }
  await pyodide.runPythonAsync(`
import sys
if "${PYAPP_ROOT}" not in sys.path:
    sys.path.insert(0, "${PYAPP_ROOT}")
import skygym_engine
`)
}

/** Boot Pyodide and the simulation core. */
async function init(): Promise<void> {
  setStatus("loading", "starting the python runtime")
  const mod = (await import(/* @vite-ignore */ `${PYODIDE_INDEX}pyodide.mjs`)) as {
    loadPyodide: (opts: { indexURL: string }) => Promise<PyodideInterface>
  }
  pyodide = await mod.loadPyodide({ indexURL: PYODIDE_INDEX })
  setStatus("loading", "loading numpy, pydantic and scipy")
  await pyodide.loadPackage(["numpy", "pydantic", "scipy"])
  setStatus("loading", "installing the simulation core")
  await installPythonSources()
  const module = pyodide.globals.get("skygym_engine") as unknown as EngineApi
  engine = {
    skygym_start: (a, b) => module.skygym_start(a, b),
    skygym_tick: () => module.skygym_tick(),
    skygym_action: (a) => module.skygym_action(a),
    skygym_finish: () => module.skygym_finish(),
    skygym_landscape: (a) => module.skygym_landscape(a),
    skygym_fork: (a, b, c, d, e) => module.skygym_fork(a, b, c, d, e),
  }
  setStatus("ready")
}

/** Parse a JSON string the Python side returned. */
function parse<T>(raw: unknown): T {
  return JSON.parse(String(raw)) as T
}

/** Advance the engine one tick and publish the frame. */
function tick(): void {
  if (!engine || finished) return
  const raw = engine.skygym_tick()
  const text = String(raw)
  if (!text) {
    finish()
    return
  }
  const frame = parse<StateFrame>(text)
  if (frame.status === "finished") {
    post({ type: "frame", frame })
    finish()
    return
  }
  post({ type: "frame", frame })
}

/**
 * The tick loop.
 *
 * The engine is deterministic, so this loop only decides *when* a tick
 * happens - never what it computes. If the worker falls behind, ticks are
 * skipped rather than bunched, so a slow machine runs slower but never
 * produces a different outcome.
 */
function loop(): void {
  if (!running) return
  const now = performance.now()
  if (now < nextTickAt) {
    timer = setTimeout(loop, nextTickAt - now)
    return
  }
  nextTickAt += TICK_MS
  if (nextTickAt < now - TICK_MS) nextTickAt = now + TICK_MS
  try {
    tick()
  } catch (error) {
    running = false
    setStatus("error", error instanceof Error ? error.message : String(error))
    return
  }
  timer = setTimeout(loop, 0)
}

/** End the session and publish the after-action-review payload. */
function finish(): void {
  if (finished) return
  finished = true
  running = false
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  if (!engine) return
  try {
    const payload = parse<{ replay: ReplaySession; outcome: unknown }>(
      engine.skygym_finish(),
    )
    const replay = payload.replay
    const last = replay.frames[replay.frames.length - 1]
    post({
      type: "end",
      frame:
        last ??
        ({
          session_id: "",
          t: replay.duration_s,
          step: 0,
          duration_s: replay.duration_s,
          status: "finished",
          tracks: [],
          sensors: [],
          effectors: [],
          events: [],
          ammo_remaining: {},
          paused: false,
          allow_pause: false,
        } as unknown as StateFrame),
      replay,
      outcome: payload.outcome,
    })
    setStatus("finished")
  } catch (error) {
    setStatus("error", error instanceof Error ? error.message : String(error))
  }
}

self.onmessage = (event: MessageEvent<EngineRequest>): void => {
  const message = event.data
  void (async () => {
    try {
      switch (message.type) {
        case "init":
          booting = init()
          await booting
          break
        case "start": {
          // The engine may still be booting: the page does not wait for the
          // "ready" status before it starts a session.
          if (booting) await booting
          if (!engine) throw new Error("the engine is not ready")
          finished = false
          running = true
          nextTickAt = performance.now()
          const raw = engine.skygym_start(JSON.stringify(message.scenario), message.sessionId)
          post({ type: "frame", frame: parse<StateFrame>(String(raw)) })
          setStatus("running")
          timer = setTimeout(loop, 0)
          break
        }
        case "action": {
          if (booting) await booting
          if (!engine) throw new Error("the engine is not ready")
          engine.skygym_action(JSON.stringify(message.action))
          break
        }
        case "finish":
          if (booting) await booting
          finish()
          break
        case "landscape": {
          if (booting) await booting
          if (!engine) throw new Error("the engine is not ready")
          const raw = engine.skygym_landscape(JSON.stringify(message.scenario))
          post({
            type: "landscape",
            requestId: message.requestId,
            spec: parse<LandscapeSpec>(String(raw)),
          })
          break
        }
        case "fork": {
          if (booting) await booting
          if (!engine) throw new Error("the engine is not ready")
          const raw = engine.skygym_fork(
            JSON.stringify(message.scenario),
            JSON.stringify(message.actions),
            JSON.stringify(message.ghostActions),
            JSON.stringify(message.request),
            JSON.stringify(message.original),
          )
          post({
            type: "fork",
            requestId: message.requestId,
            result: parse<ForkResult>(String(raw)),
          })
          break
        }
        case "dispose":
          running = false
          if (timer !== null) clearTimeout(timer)
          timer = null
          break
        default:
          break
      }
    } catch (error) {
      console.error("[engine] error:", error)
      setStatus("error", error instanceof Error ? error.message : String(error))
    }
  })()
}
