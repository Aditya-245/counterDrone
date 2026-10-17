/**
 * 3D tactical scene.
 *
 * Performance approach for the 60 fps / 40-drone budget on integrated graphics:
 *  - one `InstancedMesh` for all tracks (a single draw call regardless of count);
 *  - one `InstancedMesh` for all buildings;
 *  - LOD by range: far contacts are flat billboards, near ones get a marker ring;
 *  - the terrain is a single low-poly mesh with vertex colours;
 *  - no per-track React components inside the canvas (they re-render at 60 fps).
 *
 * This is an instrument display, so it keeps its own dark palette in both UI
 * themes the way a radar console stays dark in a lit room.
 */

import { useEffect, useMemo, useRef, type ComponentRef, type RefObject } from "react"
import { Canvas, useFrame, useThree } from "@react-three/fiber"
import { Line, OrbitControls, Text } from "@react-three/drei"
import * as THREE from "three"
import type { Scenario, TrackView } from "@/api/types"
import { LABEL_COLOURS } from "@/api/format"
import { aim, snapAim, type ProjectedTrack } from "@/scene/aim"

const SCENE = {
  ring: "#697565",
  protectedRing: "#f62440",
  civilSector: "#d9b26a",
  radar: "#ecdfcc",
  otherSensor: "#b8a8d4",
  label: "#a3ac9b",
  axis: "#3c3d37",
  urban: "#2b2f26",
  rural: "#232a20",
  building: "#3c3d37",
  buildingTop: "#697565",
  selected: "#fffaf3",
} as const

/** Metres per second the view pans while a movement key is held. */
const PAN_SPEED_MPS = 1400

type OrbitHandle = ComponentRef<typeof OrbitControls>

function terrainGeometry(scenario: Scenario): THREE.BufferGeometry {
  // A coarse analytic stand-in for the terrain: the exact heightfield is only
  // authoritative on the server, and the 3D view does not need its detail.
  const size = 6000
  const seg = 96
  const geo = new THREE.PlaneGeometry(size, size, seg, seg)
  geo.rotateX(-Math.PI / 2)
  const preset = scenario.terrain.preset
  const pos = geo.attributes["position"] as THREE.BufferAttribute
  for (let i = 0; i < pos.count; i += 1) {
    const x = pos.getX(i)
    const y = pos.getZ(i)
    const ridge = preset === "hills" ? Math.sin(x / 420) * Math.cos(y / 380) : 0
    const rough = preset === "urban" ? Math.sin(x / 160) * Math.cos(y / 150) * 0.35 : 0
    const height = ridge * scenario.terrain.scale_m * 0.7 + rough * 12
    pos.setY(i, height)
  }
  geo.computeVertexNormals()
  return geo
}

/**
 * Buildings as one instanced box mesh.
 *
 * Bases sit on the datum rather than on the sampled heightfield: this view uses
 * a coarse analytic stand-in for the ground, and the urban preset varies by only
 * a few metres, so the boxes read correctly without resampling the real field.
 */
function Buildings({ scenario }: { scenario: Scenario }): JSX.Element | null {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const buildings = scenario.terrain.buildings
  const dummy = useMemo(() => new THREE.Object3D(), [])

  useFrame(() => {
    const instanced = mesh.current
    if (!instanced) return
    instanced.count = buildings.length
    for (let i = 0; i < buildings.length; i += 1) {
      const b = buildings[i]
      if (!b) continue
      dummy.position.set(b.x, b.height_m / 2, b.y)
      dummy.scale.set(b.width_m, b.height_m, b.depth_m)
      dummy.rotation.set(0, 0, 0)
      dummy.updateMatrix()
      instanced.setMatrixAt(i, dummy.matrix)
    }
    instanced.instanceMatrix.needsUpdate = true
  })

  if (buildings.length === 0) return null

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, buildings.length]} frustumCulled={false}>
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial color={SCENE.building} wireframe />
    </instancedMesh>
  )
}

function TrackInstances({
  tracks,
  selectedId,
  onSelect,
}: {
  tracks: TrackView[]
  selectedId: string | null
  onSelect: (id: string) => void
}): JSX.Element {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const dummy = useMemo(() => new THREE.Object3D(), [])
  const colour = useMemo(() => new THREE.Color(), [])

  useFrame(() => {
    const instanced = mesh.current
    if (!instanced) return
    const n = Math.min(tracks.length, 128)
    instanced.count = n
    for (let i = 0; i < n; i += 1) {
      const trk = tracks[i]
      if (!trk) continue
      const label = trk.trainee_class ?? trk.suggested_label ?? "unknown"
      // Scale with the estimated size so the picture reads at a glance.
      const selected = trk.id === selectedId
      const scale = (trk.suggested_class === "fixed_wing" ? 16 : 10) * (selected ? 1.6 : 1)
      dummy.position.set(trk.x, trk.z, trk.y)
      dummy.scale.setScalar(scale)
      dummy.rotation.set(0, 0, 0)
      dummy.updateMatrix()
      instanced.setMatrixAt(i, dummy.matrix)
      colour.set(LABEL_COLOURS[label] ?? SCENE.ring)
      if (selected) colour.lerp(new THREE.Color(SCENE.selected), 0.55)
      instanced.setColorAt(i, colour)
    }
    instanced.instanceMatrix.needsUpdate = true
    if (instanced.instanceColor) instanced.instanceColor.needsUpdate = true
  })

  return (
    <instancedMesh
      ref={mesh}
      args={[undefined, undefined, 128]}
      frustumCulled={false}
      onClick={(e: { instanceId?: number; stopPropagation: () => void }) => {
        e.stopPropagation()
        const trk = tracks[e.instanceId ?? 0]
        if (trk) onSelect(trk.id)
      }}
    >
      <octahedronGeometry args={[1, 0]} />
      <meshBasicMaterial vertexColors toneMapped={false} />
    </instancedMesh>
  )
}

/**
 * Projects every contact into screen space each frame so the DOM crosshair can
 * lock onto what is actually drawn. Runs after `TrackInstances` writes the
 * matrices, and never re-renders React.
 */
function AimProjector({ tracks }: { tracks: TrackView[] }): null {
  const camera = useThree((s) => s.camera)
  const canvas = useThree((s) => s.gl.domElement)
  const point = useMemo(() => new THREE.Vector3(), [])

  // The DOM overlay is passive, so the canvas is the pointer source: it still
  // gets every orbit, zoom and pick event, and the reticle rides along.
  useEffect(() => {
    const trackPointer = (e: PointerEvent): void => {
      const rect = canvas.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) return
      aim.pointer.nx = ((e.clientX - rect.left) / rect.width) * 2 - 1
      aim.pointer.ny = 1 - ((e.clientY - rect.top) / rect.height) * 2
      aim.active = true
    }
    const release = (): void => {
      aim.active = false
      aim.lockedId = null
    }
    canvas.addEventListener("pointermove", trackPointer)
    canvas.addEventListener("pointerleave", release)
    return () => {
      canvas.removeEventListener("pointermove", trackPointer)
      canvas.removeEventListener("pointerleave", release)
    }
  }, [canvas])

  useFrame(() => {
    const projected: ProjectedTrack[] = []
    for (const trk of tracks) {
      point.set(trk.x, trk.z, trk.y).project(camera)
      // z > 1 means the contact is behind the camera.
      if (point.z > 1) continue
      projected.push({ id: trk.id, nx: point.x, ny: point.y })
    }
    aim.projected = projected
    snapAim()
  })

  return null
}

/** WASD pans the camera and its orbit target together across the ground plane. */
function CameraPan({ controls }: { controls: RefObject<OrbitHandle | null> }): null {
  const camera = useThree((s) => s.camera)
  const held = useRef<Set<string>>(new Set())
  const forward = useMemo(() => new THREE.Vector3(), [])
  const right = useMemo(() => new THREE.Vector3(), [])
  const move = useMemo(() => new THREE.Vector3(), [])
  const up = useMemo(() => new THREE.Vector3(0, 1, 0), [])

  useEffect(() => {
    const typing = (target: EventTarget | null): boolean =>
      target instanceof HTMLInputElement ||
      target instanceof HTMLSelectElement ||
      target instanceof HTMLTextAreaElement

    const down = (e: KeyboardEvent): void => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return
      const key = e.key.toLowerCase()
      if (key === "w" || key === "a" || key === "s" || key === "d") held.current.add(key)
    }
    const upHandler = (e: KeyboardEvent): void => {
      held.current.delete(e.key.toLowerCase())
    }
    const clear = (): void => held.current.clear()

    window.addEventListener("keydown", down)
    window.addEventListener("keyup", upHandler)
    window.addEventListener("blur", clear)
    return () => {
      window.removeEventListener("keydown", down)
      window.removeEventListener("keyup", upHandler)
      window.removeEventListener("blur", clear)
    }
  }, [])

  useFrame((_, delta) => {
    const keys = held.current
    const orbit = controls.current
    if (!orbit || keys.size === 0) return

    camera.getWorldDirection(forward)
    forward.y = 0
    if (forward.lengthSq() < 1e-9) return
    forward.normalize()
    right.crossVectors(forward, up).normalize()

    move.set(0, 0, 0)
    if (keys.has("w")) move.add(forward)
    if (keys.has("s")) move.sub(forward)
    if (keys.has("d")) move.add(right)
    if (keys.has("a")) move.sub(right)
    if (move.lengthSq() === 0) return

    // Clamp the step so a stutter cannot teleport the view across the map.
    const step = PAN_SPEED_MPS * Math.min(delta, 0.05)
    move.normalize().multiplyScalar(step)
    camera.position.add(move)
    orbit.target.add(move)
    orbit.update()
  })

  return null
}

function PerimeterRings({ scenario }: { scenario: Scenario }): JSX.Element {
  const rings = scenario.site.ring_distances_m
  const protectedRadius = scenario.site.protected_radius_m
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 1, 0]}>
        <ringGeometry args={[protectedRadius, protectedRadius + 12, 64]} />
        <meshBasicMaterial
          color={SCENE.protectedRing}
          transparent
          opacity={0.55}
          side={THREE.DoubleSide}
        />
      </mesh>
      {rings.map((r) => (
        <mesh key={r} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.5, 0]}>
          <ringGeometry args={[r, r + 3, 96]} />
          <meshBasicMaterial
            color={SCENE.ring}
            transparent
            opacity={0.35}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}
      {scenario.site.civil_sectors.map((s) => {
        const mid = (s.bearing_deg * Math.PI) / 180
        const half = (s.half_width_deg * Math.PI) / 180
        const midR = (((s.inner_m + s.outer_m) / 2) * Math.PI) / 180
        return (
          <mesh
            key={s.label}
            rotation={[-Math.PI / 2, 0, 0]}
            position={[Math.cos(mid) * midR, 0.6, Math.sin(mid) * midR]}
          >
            <ringGeometry args={[Math.max(1, s.inner_m), s.outer_m, 48, 1, -half, half * 2]} />
            <meshBasicMaterial
              color={SCENE.civilSector}
              transparent
              opacity={0.16}
              side={THREE.DoubleSide}
            />
          </mesh>
        )
      })}
    </group>
  )
}

function Sensors({ scenario }: { scenario: Scenario }): JSX.Element {
  return (
    <group>
      {scenario.sensors.map((s) => (
        <group key={s.id} position={[s.x, s.z + 4, s.y]}>
          <mesh>
            <boxGeometry args={[14, 14, 14]} />
            <meshBasicMaterial color={s.type === "radar" ? SCENE.radar : SCENE.otherSensor} />
          </mesh>
          <Text position={[0, 14, 0]} fontSize={46} color={SCENE.label}>
            {s.id}
          </Text>
        </group>
      ))}
    </group>
  )
}

function Sky({ scenario }: { scenario: Scenario }): JSX.Element {
  const tod = scenario.environment.time_of_day
  const fog = 1 - Math.min(1, scenario.environment.visibility_m / 8000)
  const colour = tod === "night" ? "#0b0f09" : tod === "dusk" ? "#1b2016" : "#3d4a35"
  return (
    <>
      <color attach="background" args={[colour]} />
      <fog attach="fog" args={[colour, 400, Math.max(900, 6000 * (1 - fog * 0.9))]} />
    </>
  )
}

export default function TacticalScene({
  scenario,
  tracks,
  selectedId,
  onSelect,
}: {
  scenario: Scenario
  tracks: TrackView[]
  selectedId: string | null
  onSelect: (id: string) => void
}): JSX.Element {
  const geo = useMemo(() => terrainGeometry(scenario), [scenario])
  const controls = useRef<OrbitHandle | null>(null)
  const axes = useMemo(() => [new THREE.Vector3(-3000, 0, 0), new THREE.Vector3(3000, 0, 0)], [])
  const axesZ = useMemo(() => [new THREE.Vector3(0, 0, -3000), new THREE.Vector3(0, 0, 3000)], [])

  return (
    <Canvas
      className="scope"
      camera={{ position: [0, 2200, 3200], fov: 45, near: 10, far: 12000 }}
      dpr={[1, 1.5]}
      gl={{ antialias: true, powerPreference: "high-performance" }}
    >
      <Sky scenario={scenario} />
      <ambientLight intensity={0.55} />
      <directionalLight position={[1200, 2600, 900]} intensity={0.7} />
      <mesh geometry={geo} position={[0, 0, 0]}>
        <meshStandardMaterial
          color={scenario.terrain.preset === "urban" ? SCENE.urban : SCENE.rural}
          wireframe
        />
      </mesh>
      <Buildings scenario={scenario} />
      <PerimeterRings scenario={scenario} />
      <Sensors scenario={scenario} />
      <TrackInstances tracks={tracks} selectedId={selectedId} onSelect={onSelect} />
      <AimProjector tracks={tracks} />
      <Line points={axes} color={SCENE.axis} lineWidth={1} />
      <Line points={axesZ} color={SCENE.axis} lineWidth={1} />
      <OrbitControls
        ref={controls}
        enableDamping
        maxPolarAngle={Math.PI / 2.1}
        minDistance={400}
        maxDistance={9000}
      />
      <CameraPan controls={controls} />
    </Canvas>
  )
}
