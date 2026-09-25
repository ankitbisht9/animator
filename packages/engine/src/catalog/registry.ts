import type { ComponentSpec } from "./spec";

const REGISTRY = new Map<string, ComponentSpec>();

export function defineComponentSpec(spec: ComponentSpec): ComponentSpec {
  if (REGISTRY.has(spec.type)) throw new Error(`duplicate component type '${spec.type}'`);
  REGISTRY.set(spec.type, spec);
  return spec;
}

export const getSpec = (type: string): ComponentSpec | undefined => REGISTRY.get(type);

export function allSpecs(): ComponentSpec[] {
  return [...REGISTRY.values()].sort((a, b) => a.type.localeCompare(b.type));
}

export const componentTypes = (): string[] => allSpecs().map((s) => s.type);
