/**
 * The generated landscape: terrain, roads, buildings, vegetation and water.
 *
 * All of it comes from `GET /api/scenarios/{id}/environment`, which the backend
 * derives deterministically from the scenario seed and the same heightfield the
 * simulation uses. Nothing is generated in the browser, so the picture and the
 * simulation can never disagree, and reloading a scenario always redraws the same
 * world.
 *
 * Draw calls: terrain 1, roads 2 (surface + markings), water 1, hedges 1, buildings
 * one per archetype, vegetation one per species, props one per kind - about 20 in
 * total, all instanced.
 */

import { useEffect, useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import type { LandscapeSpec, BuildingKind, PropKind, TreeKind } from "@/api/types"
import {
  buildingGeometry,
  hedgeGeometry,
  propGeometry,
  roadGeometry,
  roadMarkingsGeometry,
  treeGeometry,
  waterGeometry,
} from "./geometry"
import { facadeTexture, foliageTexture, roadTexture } from "./textures"
import { decodeCover, landscapeGeometry, type LandscapeGeometry } from "./terrainGrid"

const dummy = new THREE.Object3D()
const tint = new THREE.Color()

/** Terrain mesh: the server heightfield, coloured by land cover. */
function Terrain({
  geometry,
  cover,
  palette,
}: {
  geometry: LandscapeGeometry
  cover: Uint8Array
  palette: THREE.Color[]
}): JSX.Element {
  const mesh = useMemo(() => {
    const { heights, gridN, sizeM } = geometry
    const plane = new THREE.PlaneGeometry(sizeM, sizeM, gridN - 1, gridN - 1)
    plane.rotateX(-Math.PI / 2)
    const position = plane.getAttribute("position") as THREE.BufferAttribute
    const colours = new Float32Array(position.count * 3)
    const shade = new THREE.Color()
    for (let i = 0; i < position.count; i += 1) {
      const x = position.getX(i)
      const z = position.getZ(i)
      const gx = THREE.MathUtils.clamp(
        Math.round((x + sizeM / 2) / (sizeM / (gridN - 1))),
        0,
        gridN - 1,
      )
      const gy = THREE.MathUtils.clamp(
        Math.round((z + sizeM / 2) / (sizeM / (gridN - 1))),
        0,
        gridN - 1,
      )
      position.setY(i, heights[gx + gy * gridN] ?? 0)
      const index = cover[gx + gy * gridN] ?? 0
      shade.copy(palette[index] ?? palette[0] ?? new THREE.Color("#5d6b45"))
      // A touch of per-vertex variation keeps large areas from looking flat.
      const jitter = 0.9 + (0.2 * ((i * 2654435761) % 1000)) / 1000
      colours[i * 3] = shade.r * jitter
      colours[i * 3 + 1] = shade.g * jitter
      colours[i * 3 + 2] = shade.b * jitter
    }
    plane.setAttribute("color", new THREE.BufferAttribute(colours, 3))
    plane.computeVertexNormals()
    return plane
  }, [geometry, cover, palette])

  useEffect(() => () => mesh.dispose(), [mesh])

  return (
    <mesh geometry={mesh} receiveShadow castShadow={false} name="terrain">
      <meshStandardMaterial vertexColors roughness={0.96} metalness={0} dithering />
    </mesh>
  )
}

/** All roads merged into one mesh, with a second mesh for the centre lines. */
function Roads({
  spec,
  heightAt,
}: {
  spec: LandscapeSpec
  heightAt: (x: number, y: number) => number
}): JSX.Element {
  const { surface, verge, markings } = useMemo(() => {
    const surfaceParts: THREE.BufferGeometry[] = []
    const vergeParts: THREE.BufferGeometry[] = []
    const markingParts: THREE.BufferGeometry[] = []
    for (const road of spec.roads) {
      const lift = (x: number, y: number): number => heightAt(x, y) + 0.3
      const strip = roadGeometry(road.points, road.width_m, lift)
      if (strip) surfaceParts.push(strip)
      // Kerb band: wider and lower, so the carriageway reads as raised above it.
      const kerb = roadGeometry(road.points, road.width_m + 3.2, (x, y) => heightAt(x, y) + 0.16)
      if (kerb) vergeParts.push(kerb)
      if (road.markings) {
        const dashes = roadMarkingsGeometry(road.points, lift)
        if (dashes) markingParts.push(dashes)
      }
    }
    return {
      surface: mergeAll(surfaceParts),
      verge: mergeAll(vergeParts),
      markings: mergeAll(markingParts),
    }
  }, [spec, heightAt])

  useEffect(
    () => () => {
      surface?.dispose()
      verge?.dispose()
      markings?.dispose()
    },
    [surface, verge, markings],
  )

  return (
    <group>
      {verge && (
        <mesh geometry={verge} receiveShadow name="kerbs">
          <meshStandardMaterial color="#948d81" roughness={0.95} metalness={0} />
        </mesh>
      )}
      {surface && (
        <mesh geometry={surface} receiveShadow name="roads">
          <meshStandardMaterial
            map={roadTexture()}
            color="#6f6f6c"
            roughness={0.92}
            metalness={0}
          />
        </mesh>
      )}
      {markings && (
        <mesh geometry={markings} name="road-markings">
          <meshStandardMaterial
            color="#d8d2c2"
            roughness={0.8}
            polygonOffset
            polygonOffsetFactor={-2}
          />
        </mesh>
      )}
    </group>
  )
}

/** Concatenate geometries that share one material. */
function mergeAll(parts: THREE.BufferGeometry[]): THREE.BufferGeometry | null {
  if (parts.length === 0) return null
  const positions: number[] = []
  const normals: number[] = []
  const uvs: number[] = []
  const indices: number[] = []
  let vertexOffset = 0
  for (const part of parts) {
    const position = part.getAttribute("position") as THREE.BufferAttribute
    const normal = part.getAttribute("normal") as THREE.BufferAttribute
    const uv = part.getAttribute("uv") as THREE.BufferAttribute
    const index = part.getIndex()
    for (let i = 0; i < position.count; i += 1) {
      positions.push(position.getX(i), position.getY(i), position.getZ(i))
      normals.push(normal.getX(i), normal.getY(i), normal.getZ(i))
      uvs.push(uv.getX(i), uv.getY(i))
    }
    if (index) {
      for (let i = 0; i < index.count; i += 1) indices.push(vertexOffset + index.getX(i))
    } else {
      for (let i = 0; i < position.count; i += 1) indices.push(vertexOffset + i)
    }
    vertexOffset += position.count
    part.dispose()
  }
  const merged = new THREE.BufferGeometry()
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3))
  merged.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3))
  merged.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2))
  merged.setIndex(indices)
  merged.computeBoundingSphere()
  return merged
}

/** Buildings: one instanced mesh per archetype, sitting on the ground. */
function Buildings({
  spec,
  heightAt,
}: {
  spec: LandscapeSpec
  heightAt: (x: number, y: number) => number
}): JSX.Element {
  const groups = useMemo(() => {
    const byKind = new Map<BuildingKind, typeof spec.buildings>()
    for (const building of spec.buildings) {
      const bucket = byKind.get(building.kind)
      if (bucket) bucket.push(building)
      else byKind.set(building.kind, [building])
    }
    return [...byKind.entries()]
  }, [spec])

  return (
    <group name="buildings">
      {groups.map(([kind, list]) => (
        <BuildingGroup key={kind} kind={kind} items={list} heightAt={heightAt} />
      ))}
    </group>
  )
}

function BuildingGroup({
  kind,
  items,
  heightAt,
}: {
  kind: BuildingKind
  items: LandscapeSpec["buildings"]
  heightAt: (x: number, y: number) => number
}): JSX.Element {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const geometry = useMemo(() => buildingGeometry(kind), [kind])
  const texture = useMemo(() => facadeTexture(kind), [kind])

  useEffect(() => {
    const instanced = mesh.current
    if (!instanced) return
    for (let i = 0; i < items.length; i += 1) {
      const building = items[i]
      if (!building) continue
      dummy.position.set(building.x, heightAt(building.x, building.y) - 0.4, building.y)
      dummy.rotation.set(0, -THREE.MathUtils.degToRad(building.rotation_deg), 0)
      // Geometry is a unit footprint with the height baked in as a fraction, so the
      // instance scale is (width, height, depth).
      dummy.scale.set(building.width_m, building.height_m, building.depth_m)
      dummy.updateMatrix()
      instanced.setMatrixAt(i, dummy.matrix)
      // Slight tonal variety so a row of houses does not read as one material.
      const v = 0.86 + (0.24 * ((i * 97) % 11)) / 11
      tint.setRGB(v, v * 0.995, v * 0.98)
      instanced.setColorAt(i, tint)
    }
    instanced.count = items.length
    instanced.instanceMatrix.needsUpdate = true
    if (instanced.instanceColor) instanced.instanceColor.needsUpdate = true
  }, [items, heightAt])

  return (
    <instancedMesh
      ref={mesh}
      args={[geometry, undefined, Math.max(1, items.length)]}
      castShadow
      receiveShadow
      frustumCulled
    >
      <meshStandardMaterial map={texture} roughness={0.85} metalness={0.02} vertexColors={false} />
    </instancedMesh>
  )
}

/** Trees, one instanced mesh per species. */
function Vegetation({
  spec,
  heightAt,
}: {
  spec: LandscapeSpec
  heightAt: (x: number, y: number) => number
}): JSX.Element {
  const groups = useMemo(() => {
    const byKind = new Map<TreeKind, LandscapeSpec["trees"]>()
    for (const tree of spec.trees) {
      const bucket = byKind.get(tree.kind)
      if (bucket) bucket.push(tree)
      else byKind.set(tree.kind, [tree])
    }
    return [...byKind.entries()]
  }, [spec])

  return (
    <group name="vegetation">
      {groups.map(([kind, list]) => (
        <TreeGroup key={kind} kind={kind} items={list} heightAt={heightAt} />
      ))}
    </group>
  )
}

function TreeGroup({
  kind,
  items,
  heightAt,
}: {
  kind: TreeKind
  items: LandscapeSpec["trees"]
  heightAt: (x: number, y: number) => number
}): JSX.Element {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const geometry = useMemo(() => treeGeometry(kind), [kind])
  const texture = useMemo(() => foliageTexture(), [])

  useEffect(() => {
    const instanced = mesh.current
    if (!instanced) return
    for (let i = 0; i < items.length; i += 1) {
      const tree = items[i]
      if (!tree) continue
      dummy.position.set(tree.x, heightAt(tree.x, tree.y), tree.y)
      dummy.rotation.set(0, (i * 137.5 * Math.PI) / 180, 0)
      dummy.scale.set(tree.radius_m * 1.45, tree.height_m * 1.12, tree.radius_m * 1.45)
      dummy.updateMatrix()
      instanced.setMatrixAt(i, dummy.matrix)
      const v = 0.78 + (0.4 * ((i * 53) % 13)) / 13
      tint.setRGB(v * (kind === "conifer" ? 0.86 : 1), v, v * 0.82)
      instanced.setColorAt(i, tint)
    }
    instanced.count = items.length
    instanced.instanceMatrix.needsUpdate = true
    if (instanced.instanceColor) instanced.instanceColor.needsUpdate = true
  }, [items, heightAt, kind])

  return (
    <instancedMesh
      ref={mesh}
      args={[geometry, undefined, Math.max(1, items.length)]}
      castShadow
      receiveShadow
      frustumCulled
    >
      <meshStandardMaterial
        map={texture}
        color={kind === "dead" ? "#9c9080" : "#cfe6ac"}
        roughness={0.95}
        metalness={0}
      />
    </instancedMesh>
  )
}

/** Small clutter: walls, fences, hedges, lamps, vehicles, haystacks, silos. */
function Props({
  spec,
  heightAt,
  emissive,
}: {
  spec: LandscapeSpec
  heightAt: (x: number, y: number) => number
  emissive: number
}): JSX.Element {
  const groups = useMemo(() => {
    const byKind = new Map<PropKind, LandscapeSpec["props"]>()
    for (const prop of spec.props) {
      const bucket = byKind.get(prop.kind)
      if (bucket) bucket.push(prop)
      else byKind.set(prop.kind, [prop])
    }
    return [...byKind.entries()]
  }, [spec])

  return (
    <group name="props">
      {groups.map(([kind, list]) => (
        <PropGroup key={kind} kind={kind} items={list} heightAt={heightAt} emissive={emissive} />
      ))}
    </group>
  )
}

function PropGroup({
  kind,
  items,
  heightAt,
  emissive,
}: {
  kind: PropKind
  items: LandscapeSpec["props"]
  heightAt: (x: number, y: number) => number
  emissive: number
}): JSX.Element | null {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const geometry = useMemo(() => propGeometry(kind), [kind])
  const isLamp = kind === "lamp"

  useEffect(() => {
    const instanced = mesh.current
    if (!instanced) return
    for (let i = 0; i < items.length; i += 1) {
      const prop = items[i]
      if (!prop) continue
      dummy.position.set(prop.x, heightAt(prop.x, prop.y), prop.y)
      dummy.rotation.set(0, -THREE.MathUtils.degToRad(prop.rotation_deg), 0)
      const stretch = Math.max(1, prop.length_m)
      dummy.scale.set(stretch, 1, 1)
      dummy.updateMatrix()
      instanced.setMatrixAt(i, dummy.matrix)
    }
    instanced.count = items.length
    instanced.instanceMatrix.needsUpdate = true
  }, [items, heightAt])

  useFrame(() => {
    // Lamps glow at dusk and at night.
    const material = mesh.current?.material as THREE.MeshStandardMaterial | undefined
    if (material && isLamp) material.emissiveIntensity = emissive * 1.6
  })

  const colour: Record<PropKind, string> = {
    fence: "#8a7c66",
    wall: "#a49b8c",
    hedge: "#5d7a44",
    lamp: isLamp ? "#c9c2ae" : "#c9c2ae",
    vehicle: "#8d9296",
    haystack: "#c3a86a",
    silo: "#b9bcbd",
  }

  return (
    <instancedMesh
      ref={mesh}
      args={[geometry, undefined, Math.max(1, items.length)]}
      castShadow={!isLamp}
      receiveShadow
      frustumCulled
    >
      <meshStandardMaterial
        color={colour[kind]}
        roughness={0.9}
        metalness={kind === "silo" ? 0.35 : 0}
        emissive={isLamp ? "#ffd79a" : "#000000"}
        emissiveIntensity={isLamp ? emissive * 1.6 : 0}
        map={kind === "hedge" ? foliageTexture() : undefined}
      />
    </instancedMesh>
  )
}

/** Ponds and streams. */
function Water({
  spec,
  heightAt,
}: {
  spec: LandscapeSpec
  heightAt: (x: number, y: number) => number
}): JSX.Element | null {
  const geometry = useMemo(() => {
    const parts: THREE.BufferGeometry[] = []
    for (const water of spec.water) {
      const polygon = waterGeometry(water.points, heightAt, water.kind === "pond")
      if (polygon) parts.push(polygon)
    }
    return mergeAll(parts)
  }, [spec, heightAt])

  useEffect(() => () => geometry?.dispose(), [geometry])

  if (!geometry) return null
  return (
    <mesh geometry={geometry} name="water" receiveShadow>
      <meshStandardMaterial
        color="#2b4a55"
        roughness={0.12}
        metalness={0.4}
        transparent
        opacity={0.94}
        envMapIntensity={1.4}
      />
    </mesh>
  )
}

/** Hedgerows extruded from the field outlines. */
function Hedgerows({
  spec,
  heightAt,
}: {
  spec: LandscapeSpec
  heightAt: (x: number, y: number) => number
}): JSX.Element | null {
  const geometry = useMemo(() => {
    const parts: THREE.BufferGeometry[] = []
    for (const field of spec.fields) {
      for (let i = 0; i < field.points.length; i += 1) {
        const a = field.points[i]
        const b = field.points[(i + 1) % field.points.length]
        if (!a || !b) continue
        const strip = hedgeGeometry(
          [
            [a[0], a[1]],
            [b[0], b[1]],
          ],
          heightAt,
        )
        if (strip) parts.push(strip)
      }
    }
    return mergeAll(parts)
  }, [spec, heightAt])

  useEffect(() => () => geometry?.dispose(), [geometry])

  if (!geometry) return null
  return (
    <mesh geometry={geometry} name="hedgerows" castShadow receiveShadow>
      <meshStandardMaterial
        color="#4c6b3c"
        roughness={0.98}
        metalness={0}
        side={THREE.DoubleSide}
      />
    </mesh>
  )
}

/**
 * The whole environment for one scenario. Mount inside the `<Canvas>`; the scene
 * controls (rings, sensors, tracks) stay in `TacticalScene`.
 */
export default function Landscape({
  spec,
  emissive = 0,
}: {
  spec: LandscapeSpec
  emissive?: number
}): JSX.Element {
  const geometry = useMemo(() => landscapeGeometry(spec), [spec])
  const cover = useMemo(() => decodeCover(spec), [spec])
  const palette = useMemo(() => spec.palette.map((hex) => new THREE.Color(hex)), [spec.palette])

  return (
    <group name="landscape">
      <Terrain geometry={geometry} cover={cover} palette={palette} />
      <Hedgerows spec={spec} heightAt={geometry.heightAt} />
      <Roads spec={spec} heightAt={geometry.heightAt} />
      <Water spec={spec} heightAt={geometry.heightAt} />
      <Buildings spec={spec} heightAt={geometry.heightAt} />
      <Vegetation spec={spec} heightAt={geometry.heightAt} />
      <Props spec={spec} heightAt={geometry.heightAt} emissive={emissive} />
    </group>
  )
}
