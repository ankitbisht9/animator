/**
 * Materials give bodies a recognizable look and a default density (kg/m^3) for
 * the buoyancy solver. Densities are standard reference values.
 */
export const MATERIALS = {
  wood: { density: 600, base: "#d9974b", dark: "#a8662a", label: "wood" },
  cork: { density: 240, base: "#d8b27a", dark: "#a8844c", label: "cork" },
  foam: { density: 50, base: "#f3e3a0", dark: "#c9b66c", label: "foam" },
  ice: { density: 917, base: "#bfe8f6", dark: "#7cc3df", label: "ice" },
  plastic: { density: 950, base: "#ef6f9a", dark: "#bf3f6a", label: "plastic" },
  stone: { density: 2600, base: "#9aa3ad", dark: "#6b737c", label: "stone" },
  aluminium: { density: 2700, base: "#c9d2dc", dark: "#8e99a6", label: "aluminium" },
  metal: { density: 7850, base: "#8d99a8", dark: "#58626f", label: "steel" },
  gold: { density: 19300, base: "#f2c94c", dark: "#c09422", label: "gold" },
  custom: { density: 1000, base: "#f08a24", dark: "#b8601a", label: "object" },
} as const;

export type MaterialName = keyof typeof MATERIALS;
export const MATERIAL_NAMES = Object.keys(MATERIALS) as [MaterialName, ...MaterialName[]];

export function materialOf(name: unknown): (typeof MATERIALS)[MaterialName] {
  return typeof name === "string" && name in MATERIALS ? MATERIALS[name as MaterialName] : MATERIALS.custom;
}

export const FACES = ["none", "happy", "surprised", "worried", "calm", "wink"] as const;
