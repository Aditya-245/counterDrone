/**
 * 3D tactical scene.
 *
 * The world itself (terrain, roads, buildings, vegetation, water, sky) comes from
 * `scene/world`, which renders the layout the backend generated from the scenario
 * seed - the same heightfield the sensors mask against. This module keeps the
 * *instrument* layer on top: contacts, rings, sensors and the aim overlay.
 *
 * Performance approach for the 60 fps / 40-drone budget on integrated graphics:
 *  - one `InstancedMesh` for all tracks (a single draw call regardless of count);
 *  - the world is ~20 instanced or merged draw calls in total;
 *  - no per-track React components inside the canvas (they would re-render at 60 fps).
 */

import { useEffect, useMemo, useRef, type ComponentRef, type RefObject } from "react"
import { Canvas, useFrame, useThree } from "@react-three/fiber"
import { useQuery } from "@tanstack/react-query"
import { Line, OrbitControls, Text } from "@react-three/drei"
import * as THREE from "three"
import type { Scenario, TrackView } from "@/api/types"
import { LABEL_COLOURS } from "@/api/format"
import { api } from "@/api/client"
import { aim, snapAim, type ProjectedTrack } from "@/scene/aim"
import { viewRequest } from "@/scene/viewRequest"
import Landscape from "@/scene/world/Landscape"
import { landscapeGeometry } from "@/scene/world/terrainGrid"
import SkyRig from "@/scene/world/sky"

const SCENE = {
  ring: "#697565",
  protectedRing: "#f62440",
  civilSector: "#d9b26a",
  radar: "#ecdfcc",
  otherSensor: "#b8a8d4",
  label: "#a3ac9b",
  axis: "#3c3d37",
  selected: "#fffaf3",
} as const

/** Metres per second the view pans while a movement key is held. */
const PAN_SPEED_MPS = 1400

type OrbitHandle = ComponentRef<typeof OrbitControls>

function terrainGeometry(): THREE.BufferGeometry {
  // The ground is the server's own heightfield now, delivered with the landscape
  // payload (see `scene/world/Landscape.tsx`). This flat plate only covers the
  // moments before that payload arrives, so it stays deliberately cheap.
  const geo = new THREE.PlaneGeometry(6000, 6000, 4, 4)
  geo.rotateX(-Math.PI / 2)
  return geo
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

/** Height probe shared with the camera so it can never sink through the ground. */
type GroundProbe = (x: number, y: number) => number

/** Loads the generated world for a scenario and mounts it. */
function World({
  scenarioId,
  groundRef,
}: {
  scenarioId: string
  groundRef: { current: GroundProbe }
}): JSX.Element | null {
  const { data, isError } = useQuery({
    queryKey: ["landscape", scenarioId],
    queryFn: () => api.landscape(scenarioId),
    enabled: scenarioId.length > 0,
    staleTime: 5 * 60 * 1000,
  })
  const geometry = useMemo(() => (data ? landscapeGeometry(data) : null), [data])
  useEffect(() => {
    if (geometry) groundRef.current = geometry.heightAt
  }, [geometry, groundRef])
  if (isError || !data || !geometry) return null
  return <Landscape spec={data} />
}

/** WASD pans the camera and its orbit target together, never through the ground. */
function CameraPan({
  controls,
  groundRef,
}: {
  controls: RefObject<OrbitHandle | null>
  groundRef: { current: GroundProbe }
}): null {
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
    const panning = keys.size > 0
    if (orbit && panning) {
      camera.getWorldDirection(forward)
      forward.y = 0
      if (forward.lengthSq() > 1e-9) {
        forward.normalize()
        right.crossVectors(forward, up).normalize()
        move.set(0, 0, 0)
        if (keys.has("w")) move.add(forward)
        if (keys.has("s")) move.sub(forward)
        if (keys.has("d")) move.add(right)
        if (keys.has("a")) move.sub(right)
        if (move.lengthSq() > 0) {
          // Clamp the step so a stutter cannot teleport the view across the map.
          const step = PAN_SPEED_MPS * Math.min(delta, 0.05)
          move.normalize().multiplyScalar(step)
          camera.position.add(move)
          orbit.target.add(move)
          orbit.update()
        }
      }
    }

    // Keep both the eye and the orbit target above the sampled ground, so the view
    // can never end up inside a hill or under the terrain mesh.
    const ground = groundRef.current
    const floor = ground(camera.position.x, camera.position.z) + 4
    if (camera.position.y < floor) {
      camera.position.y = floor
      if (orbit) {
        orbit.target.y = Math.max(orbit.target.y, floor - 40)
        orbit.update()
      }
    }
  })

  return null
}

function PerimeterRings({ scenario }: { scenario: Scenario }): JSX.Element {
  const rings = scenario.site.ring_distances_m
  const protectedRadius = scenario.site.protected_radius_m
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 1.5, 0]}>
        <ringGeometry args={[protectedRadius, protectedRadius + 12, 64]} />
        <meshBasicMaterial
          color={SCENE.protectedRing}
          transparent
          opacity={0.55}
          side={THREE.DoubleSide}
        />
      </mesh>
      {rings.map((r) => (
        <mesh key={r} rotation={[-Math.PI / 2, 0, 0]} position={[0, 1, 0]}>
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
            position={[Math.cos(mid) * midR, 1.2, Math.sin(mid) * midR]}
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
        <group key={s.id} position={[s.x, s.z + 2, s.y]}>
          <mesh>
            <boxGeometry args={[9, 9, 9]} />
            <meshBasicMaterial color={s.type === "radar" ? SCENE.radar : SCENE.otherSensor} />
          </mesh>
          <Text position={[0, 9, 0]} fontSize={14} color={SCENE.label}>
            {s.id}
          </Text>
        </group>
      ))}
    </group>
  )
}

/**
 * Moves the camera between the three inspection views.
 *
 * Ground puts the eye at human height looking at the site, Site is the tactical
 * orbit the trainee works in, Top is a map-like overview. Transitions are damped
 * so a click glides instead of cutting.
 */
function ViewController({
  controls,
  groundRef,
}: {
  controls: RefObject<OrbitHandle | null>
  groundRef: { current: GroundProbe }
}): null {
  const camera = useThree((s) => s.camera)
  const seen = useRef(0)
  const from = useMemo(() => ({ pos: new THREE.Vector3(), target: new THREE.Vector3() }), [])
  const to = useMemo(() => ({ pos: new THREE.Vector3(), target: new THREE.Vector3() }), [])
  const blend = useRef(1)
  const start = useMemo(() => new THREE.Vector3(), [])

  useFrame(() => {
    if (viewRequest.nonce === seen.current) return
    seen.current = viewRequest.nonce
    const ground = groundRef.current
    if (viewRequest.preset === "ground") {
      to.pos.set(430, ground(430, 540) + 3.4, 540)
      to.target.set(0, ground(0, 0) + 30, 0)
    } else if (viewRequest.preset === "top") {
      to.pos.set(120, 5200, 1500)
      to.target.set(0, 0, 0)
    } else {
      to.pos.set(720, 360, 980)
      to.target.set(0, 0, 0)
    }
    from.pos.copy(camera.position)
    from.target.copy(controls.current?.target ?? new THREE.Vector3())
    blend.current = 0
    start.copy(camera.position)
  })

  useFrame((_, delta) => {
    if (blend.current >= 1) return
    blend.current = Math.min(1, blend.current + delta * 1.6)
    // Smoothstep, so the glide starts and ends gently.
    const t = blend.current * blend.current * (3 - 2 * blend.current)
    camera.position.lerpVectors(start, to.pos, t)
    const orbit = controls.current
    if (orbit) {
      orbit.target.lerpVectors(from.target, to.target, t)
      orbit.update()
    }
  })

  return null
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
  const geo = useMemo(() => terrainGeometry(), [])
  const controls = useRef<OrbitHandle | null>(null)
  const groundRef = useRef<GroundProbe>(() => 0)
  const axes = useMemo(() => [new THREE.Vector3(-3000, 0, 0), new THREE.Vector3(3000, 0, 0)], [])
  const axesZ = useMemo(() => [new THREE.Vector3(0, 0, -3000), new THREE.Vector3(0, 0, 3000)], [])

  return (
    <Canvas
      className="scope"
      shadows
      camera={{ position: [720, 360, 980], fov: 55, near: 4, far: 26000 }}
      dpr={[1, 1.5]}
      gl={{
        antialias: true,
        powerPreference: "high-performance",
        toneMapping: THREE.ACESFilmicToneMapping,
      }}
    >
      <SkyRig
        timeOfDay={scenario.environment.time_of_day}
        visibilityM={scenario.environment.visibility_m}
        shadowRadius={
          scenario.site.ring_distances_m[scenario.site.ring_distances_m.length - 1] ?? 3000
        }
      />
      <World scenarioId={scenario.id} groundRef={groundRef} />
      <mesh geometry={geo} position={[0, -0.5, 0]} visible={false}>
        <meshStandardMaterial color="#5d6b45" />
      </mesh>
      <PerimeterRings scenario={scenario} />
      <Sensors scenario={scenario} />
      <TrackInstances tracks={tracks} selectedId={selectedId} onSelect={onSelect} />
      <AimProjector tracks={tracks} />
      <Line points={axes} color={SCENE.axis} lineWidth={1} />
      <Line points={axesZ} color={SCENE.axis} lineWidth={1} />
      <OrbitControls
        ref={controls}
        enableDamping
        maxPolarAngle={Math.PI / 2.05}
        minDistance={40}
        maxDistance={12000}
      />
      <CameraPan controls={controls} groundRef={groundRef} />
      <ViewController controls={controls} groundRef={groundRef} />
    </Canvas>
  )
}
