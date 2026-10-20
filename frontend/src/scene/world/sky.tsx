/**
 * Sky, sun, fog and image-based lighting for the three times of day.
 *
 * Everything is generated locally: a gradient canvas drives the environment map
 * (so PBR surfaces have something to reflect) and drei's physical `Sky` provides
 * the dome, with the sun placed from the scenario's `time_of_day`.
 */

import { useEffect, useMemo } from "react"
import { Stars } from "@react-three/drei"
import { useThree } from "@react-three/fiber"
import * as THREE from "three"
import type { TimeOfDay } from "@/api/types"
import { skyGradient } from "./textures"
import { skyRig, SKY_RADIUS_M } from "./skyRig"

/**
 * Installs the sky dome, the sun, the fog and the environment map.
 *
 * The environment is a PMREM-filtered gradient of the same palette the dome uses,
 * which keeps reflections in step with the time of day without any HDR download.
 */
export default function SkyRig_({
  timeOfDay,
  visibilityM,
  shadowRadius,
}: {
  timeOfDay: TimeOfDay
  visibilityM: number
  shadowRadius: number
}): JSX.Element {
  const rig = skyRig(timeOfDay)
  const gl = useThree((s) => s.gl)
  const scene = useThree((s) => s.scene)

  const sunPosition = useMemo<[number, number, number]>(
    () => [rig.sun.x * 4000, rig.sun.y * 4000, rig.sun.z * 4000],
    [rig],
  )

  // Meteorological range drives haze, so a low-visibility scenario looks low
  // visibility instead of just scoring differently.
  const fogDensity = useMemo(() => {
    const range = Math.max(400, visibilityM)
    return THREE.MathUtils.clamp(0.42 / range, rig.fogDensity, 0.00016)
  }, [visibilityM, rig.fogDensity])

  useEffect(() => {
    const source = skyGradient(rig.skyTop, rig.horizon, rig.ground)
    const pmrem = new THREE.PMREMGenerator(gl)
    pmrem.compileEquirectangularShader()
    const target = pmrem.fromEquirectangular(source)
    scene.environment = target.texture
    gl.toneMappingExposure = rig.exposure
    return () => {
      scene.environment = null
      target.dispose()
      pmrem.dispose()
    }
  }, [gl, scene, rig])

  return (
    <>
      <color attach="background" args={[rig.fog]} />
      <fogExp2 attach="fog" args={[rig.fog, fogDensity]} />
      {/*
        A gradient dome rather than a physical sky: the analytic sky shader is
        expensive and renders unreliably on software rasterisers, while this costs
        one unlit sphere and always agrees with the fog colour.
      */}
      <mesh scale={[-1, 1, 1]} renderOrder={-1} frustumCulled={false} name="sky">
        <sphereGeometry args={[SKY_RADIUS_M, 32, 20]} />
        <meshBasicMaterial
          map={skyGradient(rig.skyTop, rig.horizon, rig.ground)}
          side={THREE.FrontSide}
          fog={false}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>
      {timeOfDay === "night" && <Stars radius={SKY_RADIUS_M * 0.95} count={420} fade speed={0} />}
      <hemisphereLight args={[rig.skyTop, rig.ground, rig.ambient]} />
      <directionalLight
        position={sunPosition}
        intensity={rig.sunIntensity}
        color={rig.sunColour}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-near={200}
        shadow-camera-far={shadowRadius * 3}
        shadow-camera-left={-Math.min(shadowRadius, 1600)}
        shadow-camera-right={Math.min(shadowRadius, 1600)}
        shadow-camera-top={Math.min(shadowRadius, 1600)}
        shadow-camera-bottom={-Math.min(shadowRadius, 1600)}
        shadow-bias={-0.0012}
        shadow-normalBias={3}
      />
      {/* A dim fill from the opposite side keeps shadowed facades readable. */}
      <directionalLight
        position={[-rig.sun.x * 2000, 1200, -rig.sun.z * 2000]}
        intensity={rig.sunIntensity * 0.28}
        color={rig.horizon}
      />
    </>
  )
}
