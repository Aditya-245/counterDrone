/**
 * Bundle the Python simulation core for the browser.
 *
 * The browser runs the *same* `app.core` source the server runs, so the
 * simulation is one implementation in two runtimes rather than two
 * implementations. This script copies that source into a single manifest the
 * Pyodide worker writes into its virtual filesystem.
 *
 * Output: `frontend/public/pyapp/manifest.json`
 *
 *   { "app/core/engine.py": "<source>", ... }
 *
 * Run automatically by `npm run build` and `npm run dev` (see package.json).
 */

import { readFileSync, readdirSync, mkdirSync, writeFileSync, statSync, copyFileSync } from "node:fs"
import { createHash } from "node:crypto"
import { dirname, join, relative, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(here, "..")
const repoRoot = resolve(frontendDir, "..")

const OUT_DIR = join(frontendDir, "public", "pyapp")
const OUT_FILE = join(OUT_DIR, "manifest.json")

/** Where the vendored Pyodide runtime lands, relative to the site root. */
const PYODIDE_DIR = join(frontendDir, "public", "pyodide")

/**
 * Pyodide runtime files the browser needs.
 *
 * SKYGYM is offline-first: the runtime never reaches for a CDN, so the wasm
 * module, the Python standard library and the three wheels the simulation
 * imports are copied out of `node_modules` and served from the app's own
 * origin.
 */
const PYODIDE_FILES = [
  "pyodide.mjs",
  "pyodide.asm.mjs",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
]

/**
 * The wheels `app.core` imports: numpy, pydantic and scipy.
 *
 * The set is closed over their dependencies using the Pyodide lock file, so a
 * missing transitive wheel is a build error rather than a blank screen at
 * runtime.
 */
const PYODIDE_PACKAGES = ["numpy", "pydantic", "scipy"]

/** Where the wheels are fetched from when they are not vendored yet. */
const PYODIDE_CDN =
  process.env.PYODIDE_CDN ?? "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/"

/** Directories copied verbatim into the browser's Python path. */
const SOURCES = [
  { from: join(repoRoot, "backend", "app"), to: "app" },
]

/** Individual files copied into the browser's Python path. */
const FILES = [
  { from: join(frontendDir, "src", "engine", "py", "skygym_engine.py"), to: "skygym_engine.py" },
]

function walk(dir, base = "") {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const rel = base ? `${base}/${entry}` : entry
    if (statSync(full).isDirectory()) {
      if (entry === "__pycache__") continue
      out.push(...walk(full, rel))
    } else if (entry.endsWith(".py") || entry.endsWith(".json")) {
      // `.py` is the simulation; `.json` is the data it ships with (the
      // rules-of-engagement rule sets, which `core.roe` reads from disk).
      out.push({ full, rel })
    }
  }
  return out
}

const manifest = {}

for (const source of SOURCES) {
  for (const file of walk(source.from, source.to)) {
    // `app/core` is the simulation; `app/__init__.py` is the package marker.
    if (!file.rel.startsWith("app/core/") && file.rel !== "app/__init__.py") continue
    manifest[file.rel] = readFileSync(file.full, "utf8")
  }
}
for (const file of FILES) {
  manifest[file.to] = readFileSync(file.from, "utf8")
}

mkdirSync(OUT_DIR, { recursive: true })
writeFileSync(OUT_FILE, JSON.stringify(manifest), "utf8")

const bytes = Buffer.byteLength(JSON.stringify(manifest))
console.log(
  `sync-core: ${Object.keys(manifest).length} python files -> ${relative(repoRoot, OUT_FILE)} (${(
    bytes / 1024
  ).toFixed(0)} kB)`,
)

// --------------------------------------------------------------------------- //
// The Pyodide runtime
// --------------------------------------------------------------------------- //

const pyodideSrc = join(frontendDir, "node_modules", "pyodide")
if (!statSync(pyodideSrc, { throwIfNoEntry: false })) {
  console.error(
    "sync-core: node_modules/pyodide is missing - run `npm install` in frontend first.",
  )
  process.exit(1)
}

mkdirSync(PYODIDE_DIR, { recursive: true })

let copied = 0
for (const name of PYODIDE_FILES) {
  const src = join(pyodideSrc, name)
  if (!statSync(src, { throwIfNoEntry: false })) {
    console.error(`sync-core: missing pyodide runtime file ${name}`)
    process.exit(1)
  }
  copyFileSync(src, join(PYODIDE_DIR, name))
  copied += 1
}

const lock = JSON.parse(readFileSync(join(pyodideSrc, "pyodide-lock.json"), "utf8"))

/**
 * Look a package up in the lock file.
 *
 * The lock file is not internally consistent about separators: `pydantic`
 * depends on `pydantic_core` while the key is `pydantic-core`. Both spellings
 * are tried so a lock-file quirk cannot break the build.
 */
function lookup(name) {
  const candidates = [name, name.replace(/_/g, "-"), name.replace(/-/g, "_")]
  for (const candidate of candidates) {
    const entry = lock.packages[candidate]
    if (entry) return entry
  }
  throw new Error(`pyodide lock file has no package "${name}"`)
}

/** Resolve a package plus everything it depends on. */
function closure(names) {
  const seen = new Set()
  const visit = (name) => {
    if (seen.has(name)) return
    const entry = lookup(name)
    seen.add(entry.name)
    for (const dep of entry.depends ?? []) visit(dep)
  }
  for (const name of names) visit(name)
  return [...seen]
}

const wanted = closure(PYODIDE_PACKAGES)

for (const name of wanted) {
  const entry = lookup(name)
  const target = join(PYODIDE_DIR, entry.file_name)
  if (statSync(target, { throwIfNoEntry: false })) {
    copied += 1
    continue
  }
  process.stdout.write(`sync-core: fetching ${entry.file_name} ... `)
  const response = await fetch(`${PYODIDE_CDN}${entry.file_name}`)
  if (!response.ok) {
    console.error(`\n  failed: HTTP ${response.status} for ${entry.file_name}`)
    process.exit(1)
  }
  const payload = Buffer.from(await response.arrayBuffer())
  const digest = createHash("sha256").update(payload).digest("hex")
  if (entry.sha256 && digest !== entry.sha256) {
    console.error(`\n  checksum mismatch for ${entry.file_name}`)
    console.error(`    expected ${entry.sha256}`)
    console.error(`    got      ${digest}`)
    process.exit(1)
  }
  writeFileSync(target, payload)
  console.log(`${(payload.length / 1024 / 1024).toFixed(1)} MB`)
  copied += 1
}

const runtimeBytes = readdirSync(PYODIDE_DIR).reduce(
  (sum, name) => sum + statSync(join(PYODIDE_DIR, name)).size,
  0,
)
console.log(
  `sync-core: ${copied} pyodide runtime files -> ${relative(repoRoot, PYODIDE_DIR)} (${(
    runtimeBytes /
    1024 /
    1024
  ).toFixed(1)} MB)`,
)
