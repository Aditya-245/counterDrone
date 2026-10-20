/**
 * Verify that the browser engine and the server engine agree.
 *
 * The browser runs `app.core` under Pyodide; the server runs it under CPython.
 * The determinism contract says the same scenario and the same action log
 * produce the same state hash - so the two runtimes have to agree bit for
 * bit, not merely "closely".
 *
 * This script plays every shipped scenario in the browser engine and compares
 * the hash with the one the server's own core produces.
 *
 *     node scripts/verify-engine.mjs
 */

import { execFileSync } from "node:child_process"
import { existsSync, readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

import { loadPyodide } from "pyodide"

const here = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(here, "..")
const repoRoot = resolve(frontendDir, "..")
const publicDir = join(frontendDir, "public")
const pyodideDir = join(publicDir, "pyodide")

/** Rebuild the browser's python bundle, so the check can never run on stale sources. */
function syncCore() {
  execFileSync(process.execPath, [join(here, "sync-core.mjs")], {
    cwd: frontendDir,
    stdio: "inherit",
  })
}

/** The server's own hashes, straight from the CPython core. */
function serverHashes() {
  const python = join(repoRoot, ".venv", "Scripts", "python.exe")
  const fallback = join(repoRoot, ".venv", "bin", "python")
  const exe = existsSync(python) ? python : fallback
  if (!existsSync(exe)) {
    throw new Error("the virtualenv is missing; run `make install` first")
  }
  const out = execFileSync(exe, [join(repoRoot, "backend", "scripts", "engine_hashes.py")], {
    cwd: repoRoot,
    encoding: "utf8",
  })
  return JSON.parse(out)
}

async function main() {
  syncCore()
  const expected = serverHashes()
  const ids = Object.keys(expected)
  if (ids.length === 0) throw new Error("no shipped scenarios to verify")

  console.log(`verify-engine: comparing ${ids.length} scenario(s) across runtimes`)

  const pyodide = await loadPyodide({ indexURL: `${pyodideDir}/` })
  await pyodide.loadPackage(["numpy", "pydantic", "scipy"])

  const manifest = JSON.parse(readFileSync(join(publicDir, "pyapp", "manifest.json"), "utf8"))
  pyodide.FS.mkdirTree("/pyapp")
  for (const [path, source] of Object.entries(manifest)) {
    const dir = path.split("/").slice(0, -1).join("/")
    if (dir) pyodide.FS.mkdirTree(`/pyapp/${dir}`)
    pyodide.FS.writeFile(`/pyapp/${path}`, source)
  }
  await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, "/pyapp")
import skygym_engine
`)

  let failed = 0

  // Bind the entry points once. `runPython` re-parses its argument on every
  // call, which is far slower than the browser's worker - the worker holds
  // these same handles and calls them directly.
  const api = pyodide.globals.get("skygym_engine")
  const start = api.skygym_start
  const tick = api.skygym_tick
  const finish = api.skygym_finish

  for (const id of ids) {
    const scenario = readFileSync(join(repoRoot, "scenarios", `${id}.json`), "utf8")
    const started = Date.now()
    start(scenario, "verify")
    let frame = null
    for (;;) {
      const raw = String(tick())
      if (!raw) break
      frame = JSON.parse(raw)
      if (frame.status === "finished") break
    }
    const payload = JSON.parse(String(finish()))
    const got = payload.replay.final_state_hash
    const want = expected[id]
    const ok = got === want
    if (!ok) failed += 1
    console.log(
      `  ${ok ? "ok  " : "FAIL"} ${id}: ${got} (${(Date.now() - started) / 1000}s)` +
        (ok ? "" : `\n       server: ${want}`),
    )
  }

  if (failed > 0) {
    console.error(`verify-engine: ${failed} scenario(s) disagree with the server`)
    process.exit(1)
  }
  console.log("verify-engine: the browser engine reproduces the server exactly")
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
