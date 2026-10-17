/**
 * Server-authoritative session socket with reconnect + resume (DECISIONS D015).
 *
 * The server steps the simulation at 10 Hz and pushes `state_frame` messages.
 * The client never advances the simulation; it only sends decisions and
 * interpolates between the last two frames for smooth rendering.
 */

import type { StateFrame, TraineeAction } from "./types"

export type SocketStatus = "idle" | "connecting" | "open" | "closed" | "error"

export interface SessionSocketHandlers {
  onFrame?: (frame: StateFrame) => void
  onStatus?: (status: SocketStatus) => void
  onResumed?: (frame: StateFrame, actions: TraineeAction[]) => void
  onEnd?: (frame: StateFrame) => void
  onError?: (message: string) => void
}

export interface SessionSocket {
  send: (action: TraineeAction) => void
  resume: (lastStep: number) => void
  close: () => void
  readonly status: SocketStatus
  readonly lastFrame: StateFrame | null
}

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 8000]

export function openSessionSocket(
  sessionId: string,
  handlers: SessionSocketHandlers = {},
): SessionSocket {
  let socket: WebSocket | null = null
  let closed = false
  let attempt = 0
  let status: SocketStatus = "idle"
  let lastFrame: StateFrame | null = null

  const setStatus = (next: SocketStatus): void => {
    status = next
    handlers.onStatus?.(next)
  }

  const connect = (): void => {
    if (closed) return
    setStatus("connecting")
    const proto = window.location.protocol === "https:" ? "wss" : "ws"
    const url = `${proto}://${window.location.host}/ws/session/${sessionId}`
    socket = new WebSocket(url)

    socket.onopen = () => {
      attempt = 0
      setStatus("open")
      if (lastFrame) {
        socket?.send(
          JSON.stringify({ type: "resume", last_step: lastFrame.step, schema_version: 3 }),
        )
      }
    }

    socket.onmessage = (event: MessageEvent<string>) => {
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(event.data) as Record<string, unknown>
      } catch {
        return
      }
      switch (payload["type"]) {
        case "state_frame": {
          const frame = payload["frame"] as StateFrame
          lastFrame = frame
          handlers.onFrame?.(frame)
          break
        }
        case "resumed": {
          const frame = payload["frame"] as StateFrame
          lastFrame = frame
          handlers.onResumed?.(frame, (payload["actions"] as TraineeAction[]) ?? [])
          break
        }
        case "end": {
          const frame = payload["frame"] as StateFrame
          lastFrame = frame
          handlers.onEnd?.(frame)
          setStatus("closed")
          break
        }
        case "error":
          handlers.onError?.(String(payload["detail"] ?? "unknown socket error"))
          break
        default:
          break
      }
    }

    socket.onerror = () => {
      setStatus("error")
    }

    socket.onclose = () => {
      socket = null
      if (closed) {
        setStatus("closed")
        return
      }
      const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)] ?? 8000
      attempt += 1
      window.setTimeout(connect, delay)
    }
  }

  connect()

  return {
    send: (action: TraineeAction) => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "action", action, schema_version: 3 }))
      }
    },
    resume: (lastStep: number) => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resume", last_step: lastStep, schema_version: 3 }))
      }
    },
    close: () => {
      closed = true
      socket?.close()
      socket = null
      setStatus("closed")
    },
    get status() {
      return status
    },
    get lastFrame() {
      return lastFrame
    },
  }
}

/**
 * Interpolate between two authoritative frames.
 *
 * The server runs at 10 Hz; the renderer runs at display rate. Without this the
 * whole scene would visibly step. `alpha` is in [0, 1] between `a` and `b`.
 */
export function interpolateTracks(
  a: StateFrame,
  b: StateFrame,
  alpha: number,
): StateFrame["tracks"] {
  const t = Math.min(1, Math.max(0, alpha))
  const byId = new Map(b.tracks.map((tr) => [tr.id, tr]))
  return a.tracks.map((prev) => {
    const next = byId.get(prev.id)
    if (!next) return prev
    return {
      ...next,
      x: prev.x + (next.x - prev.x) * t,
      y: prev.y + (next.y - prev.y) * t,
      z: prev.z + (next.z - prev.z) * t,
      // Everything except position comes from the newer frame: the server is
      // the authority for status, quality and suggested class.
    }
  })
}
