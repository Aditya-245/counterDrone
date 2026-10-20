/**
 * Procedural geometry for the world.
 *
 * Everything is built by hand into plain typed arrays and cached per archetype, so
 * a whole neighbourhood of houses is one instanced draw call and the project needs
 * no asset downloads or example-module imports.
 *
 * Local space convention for buildings: footprint centred on the origin, front
 * facing +z, width along x, height along y. UVs map walls into the lower 95% of the
 * facade sheet and the roof into the top strip (see `textures.ts`).
 */

import * as THREE from "three"
import type { BuildingKind, PropKind, TreeKind } from "@/api/types"

/** UV split: below this the facade sheet is wall, above it is roof. */
const WALL_V = 0.945

interface MeshBuffer {
  positions: number[]
  normals: number[]
  uvs: number[]
  indices: number[]
}

function emptyBuffer(): MeshBuffer {
  return { positions: [], normals: [], uvs: [], indices: [] }
}

function pushVertex(
  buffer: MeshBuffer,
  x: number,
  y: number,
  z: number,
  nx: number,
  ny: number,
  nz: number,
  u: number,
  v: number,
): void {
  buffer.positions.push(x, y, z)
  buffer.normals.push(nx, ny, nz)
  buffer.uvs.push(u, v)
}

function pushQuad(
  buffer: MeshBuffer,
  a: THREE.Vector3,
  b: THREE.Vector3,
  c: THREE.Vector3,
  d: THREE.Vector3,
  uScale: number,
  vScale: number,
  vOffset = 0,
): void {
  const normal = new THREE.Vector3()
    .subVectors(b, a)
    .cross(new THREE.Vector3().subVectors(d, a))
    .normalize()
  const start = buffer.positions.length / 3
  const uv = (p: THREE.Vector3): [number, number] => {
    const along = Math.abs(b.x - a.x) > Math.abs(b.z - a.z) ? p.x - a.x : p.z - a.z
    const up = p.y
    return [(along / uScale) % 1, (vOffset + up / vScale) * (1 - WALL_V) + (p.y > 0 ? WALL_V : 0)]
  }
  for (const point of [a, b, c, d]) {
    const [u, v] = uv(point)
    pushVertex(buffer, point.x, point.y, point.z, normal.x, normal.y, normal.z, u, v)
  }
  buffer.indices.push(start, start + 1, start + 2, start, start + 2, start + 3)
}

function pushBox(
  buffer: MeshBuffer,
  cx: number,
  cy: number,
  cz: number,
  width: number,
  height: number,
  depth: number,
  uvScale = 8,
): void {
  const x0 = cx - width / 2
  const x1 = cx + width / 2
  const y0 = cy
  const y1 = cy + height
  const z0 = cz - depth / 2
  const z1 = cz + depth / 2
  const v = (x: number, y: number, z: number): THREE.Vector3 => new THREE.Vector3(x, y, z)
  // Front (+z), back (-z), left, right, top, bottom.
  pushQuad(buffer, v(x0, y0, z1), v(x1, y0, z1), v(x1, y1, z1), v(x0, y1, z1), uvScale, uvScale)
  pushQuad(buffer, v(x1, y0, z0), v(x0, y0, z0), v(x0, y1, z0), v(x1, y1, z0), uvScale, uvScale)
  pushQuad(buffer, v(x0, y0, z0), v(x0, y0, z1), v(x0, y1, z1), v(x0, y1, z0), uvScale, uvScale)
  pushQuad(buffer, v(x1, y0, z1), v(x1, y0, z0), v(x1, y1, z0), v(x1, y1, z1), uvScale, uvScale)
  pushQuad(buffer, v(x0, y1, z1), v(x1, y1, z1), v(x1, y1, z0), v(x0, y1, z0), uvScale, uvScale)
  pushQuad(buffer, v(x0, y0, z0), v(x1, y0, z0), v(x1, y0, z1), v(x0, y0, z1), uvScale, uvScale)
}

/** A gable roof: two slopes meeting at a ridge, plus gable end walls. */
function pushGable(
  buffer: MeshBuffer,
  cx: number,
  base: number,
  cz: number,
  width: number,
  depth: number,
  rise: number,
): void {
  const x0 = cx - width / 2
  const x1 = cx + width / 2
  const z0 = cz - depth / 2
  const z1 = cz + depth / 2
  const y1 = base + rise
  const v = (x: number, y: number, z: number): THREE.Vector3 => new THREE.Vector3(x, y, z)
  pushQuad(
    buffer,
    v(x0, base, z1),
    v(x1, base, z1),
    v(x1, y1, cz),
    v(x0, y1, cz),
    width,
    depth,
    WALL_V,
  )
  pushQuad(
    buffer,
    v(x1, base, z0),
    v(x0, base, z0),
    v(x0, y1, cz),
    v(x1, y1, cz),
    width,
    depth,
    WALL_V,
  )
  pushTriangle(buffer, v(x0, base, z0), v(x0, base, z1), v(x0, y1, cz))
  pushTriangle(buffer, v(x1, base, z1), v(x1, base, z0), v(x1, y1, cz))
}

function pushTriangle(
  buffer: MeshBuffer,
  a: THREE.Vector3,
  b: THREE.Vector3,
  c: THREE.Vector3,
): void {
  const normal = new THREE.Vector3()
    .subVectors(b, a)
    .cross(new THREE.Vector3().subVectors(c, a))
    .normalize()
  const start = buffer.positions.length / 3
  for (const point of [a, b, c]) {
    pushVertex(buffer, point.x, point.y, point.z, normal.x, normal.y, normal.z, 0.5, WALL_V + 0.02)
  }
  buffer.indices.push(start, start + 1, start + 2)
}

/** A hip roof: four slopes to a ridge. */
function pushHip(
  buffer: MeshBuffer,
  cx: number,
  base: number,
  cz: number,
  width: number,
  depth: number,
  rise: number,
): void {
  const x0 = cx - width / 2
  const x1 = cx + width / 2
  const z0 = cz - depth / 2
  const z1 = cz + depth / 2
  const ridge = rise
  const v = (x: number, y: number, z: number): THREE.Vector3 => new THREE.Vector3(x, y, z)
  pushQuad(
    buffer,
    v(x0, base, z1),
    v(x1, base, z1),
    v(x1, base + ridge, cz),
    v(x0, base + ridge, cz),
    width,
    depth,
    WALL_V,
  )
  pushQuad(
    buffer,
    v(x1, base, z0),
    v(x0, base, z0),
    v(x0, base + ridge, cz),
    v(x1, base + ridge, cz),
    width,
    depth,
    WALL_V,
  )
  pushTriangle(buffer, v(x1, base, z1), v(x1, base, z0), v(x1, base + ridge, cz))
  pushTriangle(buffer, v(x0, base, z0), v(x0, base, z1), v(x0, base + ridge, cz))
}

/** A ring of four thin walls: parapets and boundary walls. */
function pushRing(
  buffer: MeshBuffer,
  cx: number,
  base: number,
  cz: number,
  width: number,
  depth: number,
  thickness: number,
  height: number,
): void {
  pushBox(buffer, cx, base, cz + depth / 2, width, height, thickness)
  pushBox(buffer, cx, base, cz - depth / 2, width, height, thickness)
  pushBox(buffer, cx + width / 2, base, cz, thickness, height, depth)
  pushBox(buffer, cx - width / 2, base, cz, thickness, height, depth)
}

const archetypeCache = new Map<string, THREE.BufferGeometry>()

function toGeometry(buffer: MeshBuffer): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(buffer.positions, 3))
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(buffer.normals, 3))
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(buffer.uvs, 2))
  geometry.setIndex(buffer.indices)
  geometry.computeBoundingSphere()
  return geometry
}

/**
 * Unit building geometry for `kind`: a 1x1 footprint centred on the origin, base at
 * y=0, front facing +z. Instances scale it to their real footprint and height.
 */
export function buildingGeometry(kind: BuildingKind): THREE.BufferGeometry {
  const hit = archetypeCache.get(kind)
  if (hit) return hit
  const buffer = emptyBuffer()
  switch (kind) {
    case "house_gable": {
      pushBox(buffer, 0, 0, 0, 1, 0.62, 1, 4)
      pushGable(buffer, 0, 0.62, 0, 1.04, 1.04, 0.38)
      pushBox(buffer, 0.22, 0.86, 0.16, 0.09, 0.2, 0.09, 2)
      break
    }
    case "house_hip": {
      pushBox(buffer, 0, 0, 0, 1, 0.6, 1, 4)
      pushHip(buffer, 0, 0.6, 0, 1.1, 1.1, 0.4)
      pushBox(buffer, 0.24, 0.84, 0.2, 0.08, 0.16, 0.08, 2)
      break
    }
    case "house_flat": {
      pushBox(buffer, 0, 0, 0, 1, 0.82, 1, 5)
      pushRing(buffer, 0, 0.82, 0, 1, 1, 0.05, 0.14)
      pushBox(buffer, -0.16, 0.82, 0.1, 0.24, 0.2, 0.24, 2)
      break
    }
    case "shop": {
      pushBox(buffer, 0, 0, 0, 1, 0.7, 1, 5)
      // Awning over the front, and a sign band under it.
      pushBox(buffer, 0, 0.34, 0.62, 0.86, 0.05, 0.34, 2)
      pushBox(buffer, 0, 0.26, 0.52, 0.9, 0.08, 0.06, 2)
      pushRing(buffer, 0, 0.7, 0, 1, 1, 0.04, 0.1)
      break
    }
    case "apartment": {
      pushBox(buffer, 0, 0, 0, 1, 0.94, 1, 6)
      pushRing(buffer, 0, 0.94, 0, 1, 1, 0.04, 0.08)
      // Balconies: three slabs up the front.
      pushBox(buffer, 0, 0.22, 0.53, 0.7, 0.04, 0.12, 2)
      pushBox(buffer, 0, 0.5, 0.53, 0.7, 0.04, 0.12, 2)
      pushBox(buffer, 0, 0.76, 0.53, 0.7, 0.04, 0.12, 2)
      break
    }
    case "warehouse": {
      pushBox(buffer, 0, 0, 0, 1, 0.76, 1, 8)
      pushHip(buffer, 0, 0.76, 0, 1.04, 1.04, 0.22)
      pushBox(buffer, 0, 0, 0.5, 0.42, 0.5, 0.04, 3)
      break
    }
    case "shed": {
      pushBox(buffer, 0, 0, 0, 1, 0.7, 1, 3)
      pushQuad(
        buffer,
        new THREE.Vector3(-0.52, 0.7, 0.52),
        new THREE.Vector3(0.52, 0.7, 0.52),
        new THREE.Vector3(0.52, 0.98, -0.52),
        new THREE.Vector3(-0.52, 0.98, -0.52),
        2,
        2,
        WALL_V,
      )
      break
    }
    case "tower": {
      pushBox(buffer, 0, 0, 0, 1, 0.88, 1, 7)
      pushRing(buffer, 0, 0.88, 0, 1, 1, 0.03, 0.06)
      // Setback plant room and a mast.
      pushBox(buffer, 0.06, 0.94, -0.04, 0.4, 0.06, 0.36, 2)
      pushBox(buffer, 0.06, 0.94, -0.04, 0.05, 0.14, 0.05, 2)
      break
    }
    default:
      pushBox(buffer, 0, 0, 0, 1, 0.7, 1, 4)
  }
  const geometry = toGeometry(buffer)
  archetypeCache.set(kind, geometry)
  return geometry
}

const treeCache = new Map<TreeKind, THREE.BufferGeometry>()

/** Unit tree geometry: trunk plus canopy, base at y=0, ~1 m radius canopy. */
export function treeGeometry(kind: TreeKind): THREE.BufferGeometry {
  const hit = treeCache.get(kind)
  if (hit) return hit
  const buffer = emptyBuffer()
  const trunk = new THREE.CylinderGeometry(0.055, 0.085, 0.42, 6, 1, false)
  trunk.translate(0, 0.21, 0)
  appendGeometry(buffer, trunk, 1, 1, 1)
  trunk.dispose()
  if (kind === "conifer") {
    for (const [y, r, h] of [
      [0.3, 0.42, 0.34],
      [0.55, 0.32, 0.3],
      [0.78, 0.2, 0.26],
    ] as const) {
      const cone = new THREE.ConeGeometry(r, h, 7, 1)
      cone.translate(0, y + h / 2, 0)
      appendGeometry(buffer, cone, 1, 1, 1)
      cone.dispose()
    }
  } else if (kind === "dead") {
    for (const [dx, dz, lean] of [
      [0.1, 0, 0.12],
      [-0.12, 0.06, -0.1],
      [0, -0.1, 0.16],
    ] as const) {
      const branch = new THREE.CylinderGeometry(0.02, 0.04, 0.5, 4, 1)
      branch.rotateZ(lean)
      branch.translate(dx, 0.5, dz)
      appendGeometry(buffer, branch, 1, 1, 1)
      branch.dispose()
    }
  } else {
    const crown = new THREE.IcosahedronGeometry(0.42, 0)
    crown.scale(1, 0.86, 1)
    crown.translate(0, 0.66, 0)
    appendGeometry(buffer, crown, 1, 1, 1)
    crown.dispose()
    const lower = new THREE.IcosahedronGeometry(0.28, 0)
    lower.translate(0.16, 0.46, -0.1)
    appendGeometry(buffer, lower, 1, 1, 1)
    lower.dispose()
  }
  const geometry = toGeometry(buffer)
  treeCache.set(kind, geometry)
  return geometry
}

const propCache = new Map<PropKind, THREE.BufferGeometry>()

/** Unit prop geometry per clutter kind, base at y=0, length along x. */
export function propGeometry(kind: PropKind): THREE.BufferGeometry {
  const hit = propCache.get(kind)
  if (hit) return hit
  const buffer = emptyBuffer()
  switch (kind) {
    case "hedge":
      pushBox(buffer, 0, 0, 0, 1, 0.55, 0.32, 1.5)
      break
    case "wall":
      pushBox(buffer, 0, 0, 0, 1, 0.42, 0.12, 2)
      break
    case "fence":
      for (let i = -2; i <= 2; i += 1) {
        pushBox(buffer, i * 0.22, 0, 0, 0.03, 0.32, 0.03, 1)
      }
      pushBox(buffer, 0, 0.16, 0, 1, 0.02, 0.02, 1)
      break
    case "lamp":
      pushBox(buffer, 0, 0, 0, 0.08, 1.5, 0.08, 1)
      pushBox(buffer, 0.12, 1.5, 0, 0.3, 0.05, 0.12, 1)
      break
    case "vehicle":
      pushBox(buffer, 0, 0.06, 0, 1, 0.22, 0.44, 2)
      pushBox(buffer, -0.02, 0.28, 0, 0.5, 0.18, 0.4, 2)
      break
    case "haystack":
      pushBox(buffer, 0, 0, 0, 0.6, 0.5, 0.6, 2)
      break
    case "silo":
      pushBox(buffer, 0, 0, 0, 0.42, 0.9, 0.42, 2)
      break
    default:
      pushBox(buffer, 0, 0, 0, 1, 0.2, 1, 2)
  }
  const geometry = toGeometry(buffer)
  propCache.set(kind, geometry)
  return geometry
}

/** Copy a three primitive into a MeshBuffer, scaled and translated. */
function appendGeometry(
  buffer: MeshBuffer,
  geometry: THREE.BufferGeometry,
  sx: number,
  sy: number,
  sz: number,
): void {
  const position = geometry.getAttribute("position") as THREE.BufferAttribute
  const normal = geometry.getAttribute("normal") as THREE.BufferAttribute
  const uv = geometry.getAttribute("uv") as THREE.BufferAttribute | undefined
  const index = geometry.getIndex()
  const offset = buffer.positions.length / 3
  for (let i = 0; i < position.count; i += 1) {
    pushVertex(
      buffer,
      position.getX(i) * sx,
      position.getY(i) * sy,
      position.getZ(i) * sz,
      normal.getX(i),
      normal.getY(i),
      normal.getZ(i),
      uv ? uv.getX(i) : 0.5,
      uv ? uv.getY(i) : 0.5,
    )
  }
  if (index) {
    for (let i = 0; i < index.count; i += 1) buffer.indices.push(offset + index.getX(i))
  } else {
    for (let i = 0; i < position.count; i += 1) buffer.indices.push(offset + i)
  }
}

/** Ribbon geometry along a polyline: the road surface itself. */
export function roadGeometry(
  points: [number, number][],
  width: number,
  lift: (x: number, y: number) => number,
): THREE.BufferGeometry | null {
  if (points.length < 2) return null
  const buffer = emptyBuffer()
  const half = width / 2
  for (let i = 0; i < points.length - 1; i += 1) {
    const [x0, y0] = points[i] ?? [0, 0]
    const [x1, y1] = points[i + 1] ?? [0, 0]
    const dx = x1 - x0
    const dy = y1 - y0
    const length = Math.hypot(dx, dy)
    if (length < 0.5) continue
    const nx = (-dy / length) * half
    const ny = (dx / length) * half
    const a = new THREE.Vector3(x0 - nx, lift(x0, y0), y0 - ny)
    const b = new THREE.Vector3(x1 - nx, lift(x1, y1), y1 - ny)
    const c = new THREE.Vector3(x1 + nx, lift(x1, y1), y1 + ny)
    const d = new THREE.Vector3(x0 + nx, lift(x0, y0), y0 + ny)
    pushQuad(buffer, a, b, c, d, width, 8)
  }
  return buffer.indices.length > 0 ? toGeometry(buffer) : null
}

/** Dashed centre line for a road that carries markings. */
export function roadMarkingsGeometry(
  points: [number, number][],
  lift: (x: number, y: number) => number,
): THREE.BufferGeometry | null {
  if (points.length < 2) return null
  const buffer = emptyBuffer()
  const dash = 9
  const gap = 7
  for (let i = 0; i < points.length - 1; i += 1) {
    const [x0, y0] = points[i] ?? [0, 0]
    const [x1, y1] = points[i + 1] ?? [0, 0]
    const dx = x1 - x0
    const dy = y1 - y0
    const length = Math.hypot(dx, dy)
    if (length < 0.5) continue
    const ux = dx / length
    const uy = dy / length
    const nx = -uy * 0.28
    const ny = ux * 0.28
    let travelled = (i % 2) * dash
    while (travelled < length) {
      const run = Math.min(dash, length - travelled)
      const sx = x0 + ux * travelled
      const sy = y0 + uy * travelled
      const ex = x0 + ux * (travelled + run)
      const ey = y0 + uy * (travelled + run)
      pushQuad(
        buffer,
        new THREE.Vector3(sx - nx, lift(sx, sy) + 0.12, sy - ny),
        new THREE.Vector3(ex - nx, lift(ex, ey) + 0.12, ey - ny),
        new THREE.Vector3(ex + nx, lift(ex, ey) + 0.12, ey + ny),
        new THREE.Vector3(sx + nx, lift(sx, sy) + 0.12, sy + ny),
        1,
        1,
        WALL_V,
      )
      travelled += dash + gap
    }
  }
  return buffer.indices.length > 0 ? toGeometry(buffer) : null
}

/** Flat polygon at ground height: ponds and streams. */
export function waterGeometry(
  points: [number, number][],
  lift: (x: number, y: number) => number,
  closed: boolean,
): THREE.BufferGeometry | null {
  if (points.length < 3) return null
  const buffer = emptyBuffer()
  const ring = closed ? [...points, points[0]] : points
  for (let i = 0; i < ring.length - 1; i += 1) {
    const [x0, y0] = ring[i] ?? [0, 0]
    const [x1, y1] = ring[i + 1] ?? [0, 0]
    pushQuad(
      buffer,
      new THREE.Vector3(x0, lift(x0, y0) + 0.35, y0),
      new THREE.Vector3(x1, lift(x1, y1) + 0.35, y1),
      new THREE.Vector3(x1, lift(x1, y1) + 0.35, y1 + 6),
      new THREE.Vector3(x0, lift(x0, y0) + 0.35, y0 + 6),
      6,
      6,
    )
  }
  return buffer.indices.length > 0 ? toGeometry(buffer) : null
}

/** Thin box strip along a field edge: a hedgerow. */
export function hedgeGeometry(
  points: [number, number][],
  lift: (x: number, y: number) => number,
): THREE.BufferGeometry | null {
  if (points.length < 2) return null
  const buffer = emptyBuffer()
  for (let i = 0; i < points.length - 1; i += 1) {
    const [x0, y0] = points[i] ?? [0, 0]
    const [x1, y1] = points[i + 1] ?? [0, 0]
    const dx = x1 - x0
    const dy = y1 - y0
    const length = Math.hypot(dx, dy)
    if (length < 1) continue
    const ux = dx / length
    const uy = dy / length
    const nx = -uy * 0.9
    const ny = ux * 0.9
    pushQuad(
      buffer,
      new THREE.Vector3(x0 - nx, lift(x0, y0) + 0.2, y0 - ny),
      new THREE.Vector3(x1 - nx, lift(x1, y1) + 0.2, y1 - ny),
      new THREE.Vector3(x1 - nx, lift(x1, y1) + 2.1, y1 - ny),
      new THREE.Vector3(x0 - nx, lift(x0, y0) + 2.1, y0 - ny),
      length,
      2,
    )
    pushQuad(
      buffer,
      new THREE.Vector3(x1 + nx, lift(x1, y1) + 0.2, y1 + ny),
      new THREE.Vector3(x0 + nx, lift(x0, y0) + 0.2, y0 + ny),
      new THREE.Vector3(x0 + nx, lift(x0, y0) + 2.1, y0 + ny),
      new THREE.Vector3(x1 + nx, lift(x1, y1) + 2.1, y1 + ny),
      length,
      2,
    )
  }
  return buffer.indices.length > 0 ? toGeometry(buffer) : null
}
