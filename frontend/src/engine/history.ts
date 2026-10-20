/**
 * Local session history.
 *
 * The browser played the session, so it already holds everything the
 * after-action review needs: the scenario, the action log and the captured
 * replay. Keeping them locally makes the AAR instant and available offline;
 * the server keeps the authoritative record for the instructor and for the
 * trainee's other devices.
 *
 * IndexedDB is used rather than `localStorage` because a replay is far too
 * large for a string store.
 */

import type { ReplaySession, TraineeAction } from "@/api/types"

const DB_NAME = "skygym"
const DB_VERSION = 1
const STORE = "sessions"

/** One stored session. */
export interface StoredSession {
  id: string
  traineeId: string
  scenarioId: string
  scenario: unknown
  actions: TraineeAction[]
  replay: ReplaySession
  /** The outcome summary the counterfactual fork diffs against. */
  outcome: unknown
  savedAt: number
}

function open(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null)
      return
    }
    let request: IDBOpenDBRequest
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION)
    } catch {
      resolve(null)
      return
    }
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => resolve(null)
  })
}

function run<T>(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T | null> {
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, mode)
      const request = work(tx.objectStore(STORE))
      request.onsuccess = () => resolve(request.result ?? null)
      request.onerror = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
}

/** Store a finished session locally. */
export async function saveSession(record: StoredSession): Promise<void> {
  const db = await open()
  if (!db) return
  await run(db, "readwrite", (store) => store.put(record))
  db.close()
}

/** Load one stored session, or null when it is not here. */
export async function loadSession(id: string): Promise<StoredSession | null> {
  const db = await open()
  if (!db) return null
  const record = await run<StoredSession>(db, "readonly", (store) => store.get(id))
  db.close()
  return record ?? null
}

/** Every stored session, newest first. */
export async function listLocalSessions(): Promise<StoredSession[]> {
  const db = await open()
  if (!db) return []
  const all = await run<StoredSession[]>(db, "readonly", (store) => store.getAll())
  db.close()
  return (all ?? []).sort((a, b) => b.savedAt - a.savedAt)
}

/** Forget one stored session. */
export async function deleteSession(id: string): Promise<void> {
  const db = await open()
  if (!db) return
  await run(db, "readwrite", (store) => store.delete(id))
  db.close()
}
