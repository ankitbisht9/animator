import { describe, expect, it } from "vitest";
import { type CompiledScene, compileScene, evaluateObjects, lift, solverValueAt } from "../src";
import { base } from "./helpers";

const tankScene = (
  body: Record<string, unknown>,
  solver: Record<string, unknown>,
  extra: Record<string, unknown> = {},
) =>
  base({
    duration: 8,
    objects: [
      {
        id: "tank",
        type: "container",
        params: { width: 420, height: 300, level: 0.5, wall: 10, unitsPerMeter: 1000 },
        place: { x: 800, y: 480 },
      },
      { ...body, place: { relativeTo: "tank", side: "above", gap: 20 } },
    ],
    solvers: [{ id: "buoy", type: "buoyancy", container: "tank", body: "blk", unitsPerMeter: 1000, ...solver }],
    timeline: [{ id: "run", action: "run_solver", target: "buoy", start: { at: 1 }, duration: 4 }],
    ...extra,
  });

function compileOk(raw: Record<string, unknown>): CompiledScene {
  const l = lift(raw);
  if (!l.ok || !l.scene) throw new Error(JSON.stringify(l.issues));
  const c = compileScene(l.scene);
  if (!c.ok || !c.compiled)
    throw new Error(
      JSON.stringify(
        c.issues.filter((i) => i.severity === "error"),
        null,
        1,
      ),
    );
  return c.compiled;
}

const value = (cs: CompiledScene, name: string, t: number) => {
  const s = cs.solvers.find((x) => x.id === "buoy");
  if (!s) throw new Error("no solver");
  return solverValueAt(s, name, t) as number;
};

describe("buoyancy solver", () => {
  const block = { id: "blk", type: "block", params: { width: 100, height: 100, material: "wood" } };

  it("a wooden block floats with ρ_b/ρ_f of its height under water, and F_b = W at rest", () => {
    const cs = compileOk(tankScene(block, { bodyDensity: 600 }));
    const end = 8;
    expect(cs.solvers[0]?.outcome.state).toBe("floats");
    const W = value(cs, "weight", end);
    // 0.1 m cube: V = 0.001 m³, m = 0.6 kg, W = 5.886 N
    expect(W).toBeCloseTo(0.6 * 9.81, 6);
    expect(value(cs, "buoyantForce", end)).toBeCloseTo(W, 3);
    expect(value(cs, "submergedFraction", end)).toBeCloseTo(0.6, 3);
    // Level rise = displaced volume / tank area: 0.0006 m³ / (0.4 m)² = 3.75 mm
    expect(value(cs, "levelRise", end)).toBeCloseTo(0.0006 / 0.16, 5);
  });

  it("a stone sinks: buoyant force is ρ_f·V·g and less than its weight", () => {
    const cs = compileOk(tankScene({ ...block, params: { width: 100, height: 100, material: "stone" } }, {}));
    expect(cs.solvers[0]?.outcome.state).toBe("sinks");
    const fb = value(cs, "buoyantForce", 8);
    expect(fb).toBeCloseTo(1000 * 0.001 * 9.81, 4);
    expect(value(cs, "apparentWeight", 8)).toBeCloseTo(2600 * 0.001 * 9.81 - fb, 4);
    const bodyBottom = evaluateObjects(cs, 8).bboxOf("blk");
    const floor = evaluateObjects(cs, 8).anchorOf("tank", "floor");
    expect((bodyBottom?.y ?? 0) + (bodyBottom?.h ?? 0)).toBeCloseTo(floor?.y ?? 0, 3);
  });

  it("drives the water level, body position and ripples as pure functions of time", () => {
    const cs = compileOk(tankScene(block, { bodyDensity: 600 }));
    const levelAt = (t: number) => Number(evaluateObjects(cs, t).objects.get("tank")?.params.level);
    expect(levelAt(0.5)).toBeCloseTo(0.5, 6);
    expect(levelAt(8)).toBeGreaterThan(0.5);
    expect(levelAt(3)).toBe(levelAt(3));
    const ripple = (t: number) => Number(evaluateObjects(cs, t).objects.get("tank")?.params.ripple);
    expect(Math.max(...[1.8, 2, 2.2, 2.4].map(ripple))).toBeGreaterThan(1);
  });

  it("overflow: the weight of spilled water equals the buoyant force (Archimedes)", () => {
    const cs = compileOk(
      base({
        duration: 8,
        objects: [
          {
            id: "can",
            type: "container",
            params: {
              shape: "overflow_can",
              width: 300,
              height: 320,
              level: 0.7,
              spoutLevel: 0.7,
              wall: 10,
              unitsPerMeter: 1000,
            },
            place: { x: 700, y: 480 },
          },
          {
            id: "cup",
            type: "container",
            params: { shape: "beaker", width: 160, height: 160, level: 0, wall: 8, unitsPerMeter: 1000 },
            place: { x: 1060, y: 560 },
          },
          {
            id: "blk",
            type: "block",
            params: { width: 90, height: 90, material: "stone" },
            place: { relativeTo: "can", side: "above", gap: 20 },
          },
        ],
        solvers: [
          { id: "buoy", type: "buoyancy", container: "can", body: "blk", catchContainer: "cup", unitsPerMeter: 1000 },
        ],
        timeline: [{ action: "run_solver", target: "buoy", start: { at: 0.5 }, duration: 5 }],
      }),
    );
    expect(value(cs, "spilledWeight", 8)).toBeCloseTo(value(cs, "buoyantForce", 8), 3);
    expect(Number(evaluateObjects(cs, 8).objects.get("cup")?.params.level)).toBeGreaterThan(0);
  });

  it("lowering on a spring scale: the reading drops by the buoyant force", () => {
    const cs = compileOk(
      tankScene(
        { id: "blk", type: "block", params: { width: 80, height: 80, material: "metal" } },
        { mode: "lower", holdDepth: 1, scale: "sc" },
        {
          objects: [
            {
              id: "tank",
              type: "container",
              params: { width: 420, height: 300, level: 0.5, wall: 10, unitsPerMeter: 1000 },
              place: { x: 800, y: 520 },
            },
            { id: "sc", type: "spring_scale", params: { max: 50 }, place: { x: 760, y: 170 } },
            {
              id: "blk",
              type: "block",
              params: { width: 80, height: 80, material: "metal" },
              place: { relativeTo: "tank", side: "above", gap: 10 },
            },
          ],
        },
      ),
    );
    const reading = (t: number) => Number(evaluateObjects(cs, t).objects.get("sc")?.params.reading);
    const W = 7850 * 0.000512 * 9.81;
    expect(reading(0.9)).toBeCloseTo(W, 3);
    expect(reading(8)).toBeCloseTo(W - 1000 * 0.000512 * 9.81, 3);
  });

  it("rejects a body that does not fit or is not above the container", () => {
    const l = lift(tankScene({ id: "blk", type: "block", params: { width: 500, height: 80 } }, {}));
    const codes = l.scene ? compileScene(l.scene).issues.map((i) => i.code) : [];
    expect(codes).toContain("SOLVER_BODY_TOO_BIG");
  });

  it("flags narration that contradicts the physics", () => {
    const raw = tankScene(
      { ...block, params: { width: 100, height: 100, material: "stone" } },
      {},
      { narration: { beats: [{ id: "b", text: "Watch the stone **float**!" }] } },
    );
    const l = lift(raw);
    const codes = l.scene ? compileScene(l.scene).issues.map((i) => i.code) : [];
    expect(codes).toContain("NARRATION_CONTRADICTS_SOLVER");
    const ok = lift(
      tankScene(
        { ...block, params: { width: 100, height: 100, material: "stone" } },
        {},
        { narration: { beats: [{ id: "b", text: "Will it float or sink?" }] } },
      ),
    );
    expect(ok.scene ? compileScene(ok.scene).issues.map((i) => i.code) : []).not.toContain(
      "NARRATION_CONTRADICTS_SOLVER",
    );
  });

  it("is deterministic", () => {
    const a = compileOk(tankScene(block, { bodyDensity: 600 }));
    const b = compileOk(tankScene(block, { bodyDensity: 600 }));
    expect(JSON.stringify(a.solvers)).toBe(JSON.stringify(b.solvers));
  });
});

describe("mechanics and flow solvers", () => {
  it("projectile range matches v² sin 2θ / g without drag", () => {
    const cs = compileOk(
      base({
        duration: 6,
        objects: [{ id: "ball", type: "ball", params: { radius: 16 }, place: { x: 200, y: 600 } }],
        solvers: [{ id: "buoy", type: "projectile", body: "ball", speed: 12, angle: 40, unitsPerMeter: 50 }],
        timeline: [{ action: "run_solver", target: "buoy", start: { at: 0.5 } }],
      }),
    );
    const s = cs.solvers[0];
    expect(s?.constants.range).toBeCloseTo((144 * Math.sin((80 * Math.PI) / 180)) / 9.81, 1);
    expect(s?.constants.maxHeight).toBeCloseTo((144 * Math.sin((40 * Math.PI) / 180) ** 2) / (2 * 9.81), 2);
  });

  it("pendulum period matches 2π√(L/g) for small angles", () => {
    const cs = compileOk(
      base({
        duration: 10,
        objects: [{ id: "p", type: "pendulum", params: { length: 200, angle: 5 }, place: { x: 800, y: 200 } }],
        solvers: [{ id: "buoy", type: "pendulum", pendulum: "p", length: 1, angle: 5 }],
        timeline: [{ action: "run_solver", target: "buoy", start: { at: 0 } }],
      }),
    );
    const s = cs.solvers[0];
    if (!s) throw new Error("no solver");
    const angles = s.channels.angle ?? [];
    // First return to the starting angle (maximum) is one period.
    let peak = 0;
    for (let i = 10; i < angles.length - 1; i++) {
      if ((angles[i] as number) >= (angles[i - 1] as number) && (angles[i] as number) >= (angles[i + 1] as number)) {
        peak = i;
        break;
      }
    }
    expect(peak * s.dt).toBeCloseTo(2 * Math.PI * Math.sqrt(1 / 9.81), 1);
  });

  it("spring-mass returns to its amplitude after one undamped period", () => {
    const cs = compileOk(
      base({
        duration: 10,
        objects: [
          { id: "m", type: "block", params: { width: 60, height: 60 }, place: { x: 800, y: 500 } },
          { id: "sp", type: "spring", params: { from: { x: 800, y: 200 }, to: "m.attach_top" } },
        ],
        solvers: [{ id: "buoy", type: "spring_mass", spring: "sp", body: "m", stiffness: 40, mass: 1, amplitude: 0.1 }],
        timeline: [{ action: "run_solver", target: "buoy", start: { at: 0 } }],
      }),
    );
    const s = cs.solvers[0];
    if (!s) throw new Error("no solver");
    const T = s.constants.period as number;
    expect(T).toBeCloseTo(2 * Math.PI * Math.sqrt(1 / 40), 6);
    const i = Math.round(T / s.dt);
    expect(s.channels.displacement?.[i]).toBeCloseTo(0.1, 2);
  });

  it("fluid flow obeys continuity and Bernoulli", () => {
    const cs = compileOk(
      base({
        duration: 6,
        objects: [
          {
            id: "pipe",
            type: "pipe",
            params: {
              sections: [
                { id: "a", x: 0, width: 160 },
                { id: "b", x: 400, width: 80 },
                { id: "c", x: 800, width: 160 },
              ],
            },
            place: { slot: "stage.center" },
          },
        ],
        solvers: [
          { id: "buoy", type: "fluid_flow", pipe: "pipe", flowRate: 0.01, inletPressure: 150000, unitsPerMeter: 1000 },
        ],
        values: [{ id: "dp", expr: "buoy.p_a - buoy.p_b" }],
      }),
    );
    const c = cs.solvers[0]?.constants ?? {};
    expect((c.v_b as number) / (c.v_a as number)).toBeCloseTo(4, 4);
    const energy = (k: string) => (c[`p_${k}`] as number) + 0.5 * 1000 * (c[`v_${k}`] as number) ** 2;
    // Constants are rounded to 4-6 decimals, so compare to 0.01 Pa.
    expect(energy("b")).toBeCloseTo(energy("a"), 2);
    expect(cs.values.dp).toBeGreaterThan(0);
  });
});
