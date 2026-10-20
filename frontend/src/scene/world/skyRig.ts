/**
 * Lighting presets per time of day.
 *
 * Split out of the component file so `sky.tsx` only exports its component; the
 * values are data, not React.
 */

import * as THREE from "three"
import type { TimeOfDay } from "@/api/types"

/** Radius of the sky dome, comfortably outside the 6 km world. */
export const SKY_RADIUS_M = 13_000

export interface SkyRig {
  /** Direction *towards* the sun, in world space. */
  sun: THREE.Vector3
  sunColour: string
  sunIntensity: number
  skyTop: string
  horizon: string
  ground: string
  fog: string
  fogDensity: number
  ambient: number
  /** Lamp and window emissive strength; only meaningful at night. */
  emissive: number
  exposure: number
}

const RIGS: Record<TimeOfDay, SkyRig> = {
  day: {
    sun: new THREE.Vector3(0.42, 0.78, 0.46).normalize(),
    sunColour: "#fff4e0",
    sunIntensity: 2.4,
    skyTop: "#7ba3cc",
    horizon: "#c3d3dd",
    ground: "#6f6a5c",
    fog: "#b9c7cf",
    fogDensity: 0.00006,
    ambient: 0.9,
    emissive: 0,
    exposure: 1.12,
  },
  dusk: {
    sun: new THREE.Vector3(0.86, 0.14, 0.24).normalize(),
    sunColour: "#ffb066",
    sunIntensity: 1.5,
    skyTop: "#3d5a80",
    horizon: "#d99a63",
    ground: "#4a4438",
    fog: "#a98a72",
    fogDensity: 0.0001,
    ambient: 0.7,
    emissive: 0.35,
    exposure: 1.05,
  },
  night: {
    sun: new THREE.Vector3(-0.5, 0.62, -0.4).normalize(),
    sunColour: "#9fb6d8",
    sunIntensity: 0.35,
    skyTop: "#0a1122",
    horizon: "#16203a",
    ground: "#0d1018",
    fog: "#131b2c",
    fogDensity: 0.00013,
    ambient: 0.5,
    emissive: 1,
    exposure: 1.25,
  },
}

/** The lighting preset for a time of day. */
export function skyRig(timeOfDay: TimeOfDay): SkyRig {
  return RIGS[timeOfDay] ?? RIGS.day
}
