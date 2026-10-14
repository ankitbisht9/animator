/**
 * Objects drawn from their endpoints, and what they join (ADR-060): arrows in a group land on the
 * anchors they name, arrows aimed at an object's centre stop on its outline, corners of round objects
 * are on the round outline, and nodes of one diagram drawn into each other are flagged.
 */
import { describe, expect, it } from "vitest";
import { compileScene, evaluateObjects, sceneEndState, type Vec2 } from "../src";
import { compiled, lifted } from "./helpers";

const circle = (id: string, x: number, y: number, radius = 40, extra: Record<string, unknown> = {}) => ({
  id,
  type: "circle",
  params: { radius },
  place: { x, y },
  ...extra,
});
const arrow = (id: string, from: unknown, to: unknown, extra: Record<string, unknown> = {}) => {
  const { parent, ...params } = extra;
  return { id, type: "arrow", params: { from, to, ...params }, ...(parent ? { parent } : {}) };
};
const at = (objects: unknown[], id: string, anchor: string, t = 3, extra: Record<string, unknown> = {}): Vec2 => {
  const p = evaluateObjects(compiled({ objects, ...extra }), t).objects.get(id)?.anchors[anchor];
  if (!p) throw new Error(`no anchor ${id}.${anchor}`);
  return p;
};
const near = (p: Vec2, x: number, y: number) => {
  expect(p.x).toBeCloseTo(x, 1);
  expect(p.y).toBeCloseTo(y, 1);
};

describe("objects drawn from their endpoints, in a group", () => {
  const group = { id: "g", type: "group", params: {}, place: { x: 674.4, y: 346.5 }, scale: 0.9897 };

  it("an arrow between members lands on the anchors it names, wherever the group is", () => {
    const objects = [
      group,
      circle("a", 400, 230, 36, { parent: "g" }),
      circle("b", 560, 230, 36, { parent: "g" }),
      arrow("ab", "a.right", "b.left", { parent: "g" }),
    ];
    const cs = compiled({ objects });
    const ev = evaluateObjects(cs, 3).objects;
    const a = ev.get("a")?.anchors.right as Vec2;
    const b = ev.get("b")?.anchors.left as Vec2;
    near(ev.get("ab")?.anchors.start as Vec2, a.x, a.y);
    near(ev.get("ab")?.anchors.end as Vec2, b.x, b.y);
  });

  it("raw points of a member stay in the group's frame (parts of a figure, by offsets)", () => {
    const objects = [
      { id: "junction", type: "group", params: {}, place: { x: 700, y: 450 } },
      {
        id: "road",
        type: "rectangle",
        params: { width: 400, height: 120 },
        place: { x: 700, y: 450 },
        parent: "junction",
      },
      { id: "lane", type: "line", params: { from: { x: -180, y: 0 }, to: { x: 180, y: 0 } }, parent: "junction" },
    ];
    const box = evaluateObjects(compiled({ objects }), 3).objects.get("lane")?.layoutBBox;
    expect(box).toBeDefined();
    if (!box) return;
    expect(box.x + box.w / 2).toBeCloseTo(700, 0);
    expect(box.y + box.h / 2).toBeCloseTo(450, 0);
    expect(box.w).toBeGreaterThan(355);
  });

  it("an arrow between members stays on them while the group moves", () => {
    const objects = [
      group,
      circle("a", 400, 230, 36, { parent: "g" }),
      circle("b", 560, 230, 36, { parent: "g" }),
      arrow("ab", "a.bottom", "b.bottom", { parent: "g", curve: 40 }),
    ];
    const timeline = [{ id: "m", action: "move", target: "g", by: { x: 120, y: 80 }, start: { at: 1 }, duration: 2 }];
    const cs = compiled({ objects, timeline });
    for (const t of [0.5, 2, 4]) {
      const ev = evaluateObjects(cs, t).objects;
      const a = ev.get("a")?.anchors.bottom as Vec2;
      const b = ev.get("b")?.anchors.bottom as Vec2;
      near(ev.get("ab")?.anchors.start as Vec2, a.x, a.y);
      near(ev.get("ab")?.anchors.end as Vec2, b.x, b.y);
    }
  });

  it("an arrow put in a group by the `group` action stays on its members when the group moves", () => {
    const objects = [circle("a", 400, 500), circle("b", 700, 500), arrow("ab", "a.right", "b.left")];
    const timeline = [
      { id: "g", action: "group", target: "pair", members: ["a", "b", "ab"], start: { at: 1 } },
      { id: "m", action: "move", target: "pair", by: { x: 0, y: -120 }, start: { at: 2 }, duration: 1 },
    ];
    const ev = evaluateObjects(compiled({ objects, timeline }), 4).objects;
    near(ev.get("ab")?.anchors.start as Vec2, 440, 380);
    near(ev.get("ab")?.anchors.end as Vec2, 660, 380);
  });

  it("a marker in a group rings its target where the target is", () => {
    const objects = [
      group,
      circle("a", 400, 230, 36, { parent: "g" }),
      { id: "ring", type: "marker", params: { target: "a", padding: 10 }, parent: "g" },
    ];
    const ev = evaluateObjects(compiled({ objects }), 3).objects;
    const a = ev.get("a")?.layoutBBox;
    const ring = ev.get("ring")?.layoutBBox;
    expect(a && ring).toBeTruthy();
    if (!a || !ring) return;
    expect(ring.x + ring.w / 2).toBeCloseTo(a.x + a.w / 2, 0);
    expect(ring.y + ring.h / 2).toBeCloseTo(a.y + a.h / 2, 0);
    expect(ring.w).toBeLessThan(a.w + 30);
  });
});

describe("arrows meet the outlines of what they join", () => {
  const two = [circle("a", 400, 500), circle("b", 700, 500)];

  it("an arrow between two centres stops on both circles", () => {
    const objects = [...two, arrow("ab", "a.center", "b.center")];
    near(at(objects, "ab", "start"), 440, 500);
    near(at(objects, "ab", "end"), 660, 500);
  });

  it("a gap the model widened past the outline is kept", () => {
    const objects = [...two, arrow("ab", "a.center", "b.center", { startGap: 50, endGap: 64 })];
    near(at(objects, "ab", "start"), 450, 500);
    near(at(objects, "ab", "end"), 636, 500);
  });

  it("a pointer from a free point stops on the outline; a force from a centre keeps its tail", () => {
    near(at([...two, arrow("p", { x: 100, y: 500 }, "a.center")], "p", "end"), 360, 500);
    near(at([...two, arrow("f", "a.center", { x: 400, y: 800 })], "f", "start"), 400, 500);
  });

  it("a corner of a circle is on the circle", () => {
    const k = 40 * Math.SQRT1_2;
    const objects = [...two, arrow("ab", "a.top_right", "b.top_left")];
    near(at(objects, "ab", "start"), 400 + k, 500 - k);
    near(at(objects, "ab", "end"), 700 - k, 500 - k);
  });

  it("stops on the edge of a polygon and of a box", () => {
    const diamond = { id: "d", type: "polygon", params: { sides: 4, radius: 60 }, place: { x: 700, y: 500 } };
    near(at([circle("a", 400, 500), diamond, arrow("ad", "a.center", "d.center")], "ad", "end"), 640, 500);
    const card = { id: "r", type: "rectangle", params: { width: 200, height: 100 }, place: { x: 800, y: 500 } };
    near(at([card, arrow("p", { x: 400, y: 500 }, "r.center")], "p", "end"), 700, 500);
  });

  it("points into a region larger than a node, and leaves an arrow from inside alone", () => {
    const big = circle("earth", 900, 500, 200);
    near(at([big, arrow("p", { x: 300, y: 500 }, "earth.center")], "p", "end"), 900, 500);
    const objects = [circle("a", 400, 500), arrow("r", "a.center", "a.right")];
    near(at(objects, "r", "start"), 400, 500);
    near(at(objects, "r", "end"), 440, 500);
  });

  it("leaves an arrow between objects drawn into each other as written", () => {
    const objects = [circle("a", 400, 500), circle("b", 470, 500), arrow("ab", "a.center", "b.center")];
    near(at(objects, "ab", "start"), 400, 500);
    near(at(objects, "ab", "end"), 470, 500);
  });

  it("follows an arrow in a scaled group (gaps in the group's units)", () => {
    const objects = [
      { id: "g", type: "group", params: {}, place: { x: 600, y: 400 }, scale: 0.5 },
      circle("a", 400, 500, 40, { parent: "g" }),
      circle("b", 700, 500, 40, { parent: "g" }),
      arrow("ab", "a.center", "b.center", { parent: "g" }),
    ];
    const ev = evaluateObjects(compiled({ objects }), 3).objects;
    const a = ev.get("a")?.anchors.right as Vec2;
    const b = ev.get("b")?.anchors.left as Vec2;
    near(ev.get("ab")?.anchors.start as Vec2, a.x, a.y);
    near(ev.get("ab")?.anchors.end as Vec2, b.x, b.y);
  });

  it("does not carry the widened gaps into the next scene", () => {
    const cs = compiled({ objects: [...two, arrow("ab", "a.center", "b.center", { endGap: 8 })] });
    const params = sceneEndState(cs, null).objects.ab?.params ?? {};
    expect(params.endGap).toBe(8);
    expect(params.__endGap).toBeUndefined();
    expect(params.__startGap).toBeUndefined();
    expect(params.from).toBe("a.center");
  });
});

describe("nodes of one diagram drawn into each other", () => {
  const label = (id: string, target: string) => ({
    id,
    type: "label",
    params: { text: id.slice(0, 1).toUpperCase() },
    place: { relativeTo: target, side: "above" },
  });
  const issuesOf = (objects: unknown[], timeline: unknown[] = []) =>
    compileScene(lifted({ objects, timeline })).issues.filter((i) => i.code === "NODE_OVERLAP");

  it("flags a node drawn right on another one, each labelled on its own, with a move that clears it", () => {
    const found = issuesOf([
      circle("commit_d", 700, 500, 30),
      circle("commit_d_prime", 700, 500, 30),
      label("d_label", "commit_d"),
      label("e_label", "commit_d_prime"),
    ]);
    expect(found).toHaveLength(1);
    expect(found[0]?.message).toContain("'commit_d_prime' is drawn over 'commit_d'");
    expect(found[0]?.fix).toBeDefined();
  });

  it("flags members of one group joined by arrows", () => {
    const found = issuesOf([
      { id: "graph", type: "group", params: {}, place: { x: 640, y: 450 } },
      circle("node_a", 600, 450, 30, { parent: "graph" }),
      circle("node_b", 625, 450, 30, { parent: "graph" }),
      circle("node_c", 900, 450, 30, { parent: "graph" }),
      arrow("ab", "node_a.right", "node_c.left", { parent: "graph" }),
      arrow("bc", "node_b.bottom", "node_c.bottom", { parent: "graph", curve: 60 }),
    ]);
    expect(found.map((i) => i.message.slice(0, 30))).toHaveLength(1);
  });

  it("leaves parts of one drawing alone: unlabelled puffs, different kinds, a light touch", () => {
    expect(issuesOf([circle("cloud_a", 600, 400, 50), circle("cloud_b", 650, 400, 50)])).toHaveLength(0);
    const box = { id: "commit_box", type: "rectangle", params: { width: 60, height: 60 }, place: { x: 700, y: 500 } };
    expect(
      issuesOf([circle("commit_c", 700, 500, 30), box, label("c_label", "commit_c"), label("x_label", "commit_box")]),
    ).toHaveLength(0);
    expect(
      issuesOf([
        circle("node_a", 600, 450, 30),
        circle("node_b", 656, 450, 30),
        label("a_label", "node_a"),
        label("b_label", "node_b"),
      ]),
    ).toHaveLength(0);
  });

  it("leaves berries of a bunch (a partial overlap) and parts placed on a shape (a door's handle) alone", () => {
    expect(
      issuesOf([
        circle("grape_berry_4", 300, 420, 46),
        circle("grape_berry_6", 270, 475, 46),
        label("g_label", "grape_berry_4"),
        label("h_label", "grape_berry_6"),
      ]),
    ).toHaveLength(0);
    const part = (id: string, on: string) => ({
      id,
      type: "rectangle",
      params: { width: 16, height: 60 },
      place: { relativeTo: on, side: "center" },
    });
    expect(
      issuesOf([
        { id: "fridge_cavity", type: "rectangle", params: { width: 340, height: 480 }, place: { x: 700, y: 450 } },
        { id: "fridge_door", type: "rectangle", params: { width: 300, height: 470 }, place: { x: 700, y: 450 } },
        part("fridge_handle", "fridge_door"),
        part("fridge_shelf", "fridge_cavity"),
      ]),
    ).toHaveLength(0);
  });

  it("moves a whole card clear when the cards of a stack are piled on one spot", () => {
    const card = (n: number) => [
      { id: `card_${n}`, type: "group", params: {}, place: { x: 518, y: 426 } },
      {
        id: `card_${n}_body`,
        type: "rectangle",
        params: { width: 290, height: 92 },
        place: { x: 518, y: 426 },
        parent: `card_${n}`,
      },
      {
        id: `card_${n}_text`,
        type: "text",
        params: { text: `countdown(${n})` },
        place: { relativeTo: `card_${n}_body`, side: "center" },
        parent: `card_${n}`,
      },
    ];
    const found = issuesOf([...card(1), ...card(2)]);
    expect(found).toHaveLength(1);
    expect(found[0]?.path).toBe("/objects/card_2");
    expect(found[0]?.fix).toBeDefined();
  });

  it("does not flag a node passing over another while it moves", () => {
    const objects = [
      circle("commit_c", 400, 500, 30),
      circle("commit_c_prime", 400, 500, 30),
      label("c_label", "commit_c"),
      label("p_label", "commit_c_prime"),
    ];
    const timeline = [
      { id: "m", action: "move", target: "commit_c_prime", to: { x: 800, y: 500 }, start: { at: 0 }, duration: 3 },
    ];
    const found = issuesOf(objects, timeline);
    expect(found.every((i) => (i.time ?? 0) <= 0.1)).toBe(true);
  });
});

describe("a path drawn away from the points it was written through", () => {
  const commit = (id: string, x: number, y: number) => circle(id, x, y, 22);
  const commits = [commit("c0", 900, 500), commit("c1", 1045, 550), commit("c2", 1190, 550)];
  const lane = (d: string, x: number, y: number) => ({
    id: "lane",
    type: "path",
    params: { d, closed: false },
    place: { x, y },
  });
  const found = (objects: unknown[], timeline: unknown[] = []) =>
    compileScene(lifted({ objects, timeline })).issues.filter((i) => i.code === "PATH_OFF_ITS_POINTS");

  it("moves a branch line written from its place (0,0 there) onto its commits", () => {
    const hits = found([...commits, lane("M 0 0 L 145 50 L 290 50", 900, 500)]);
    expect(hits).toHaveLength(1);
    expect(hits[0]?.path).toBe("/objects/lane");
    expect(hits[0]?.fix).toMatchObject({ dx: 145, dy: 25 });
    expect(found([...commits, lane("M 0 0 L 145 50 L 290 50", 1045, 525)])).toHaveLength(0);
  });

  it("moves a line written on the canvas, placed somewhere else, onto its points", () => {
    const hits = found([...commits, lane("M 900 500 L 1045 550 L 1190 550", 600, 300)]);
    expect(hits).toHaveLength(1);
    expect(hits[0]?.message).toContain("on the canvas");
    expect(hits[0]?.fix).toMatchObject({ dx: 445, dy: 225 });
  });

  it("leaves a path alone when no reading lands it on two more things, or when it moves", () => {
    expect(found([...commits, lane("M 0 0 L 145 50 L 290 300", 300, 300)])).toHaveLength(0);
    expect(found([commit("c0", 900, 500), lane("M 0 0 L 145 50 L 290 50", 900, 500)])).toHaveLength(0);
    const moves = [{ id: "m", action: "move", target: "lane", by: { x: 0, y: 40 }, start: { at: 1 }, duration: 1 }];
    expect(found([...commits, lane("M 0 0 L 145 50 L 290 50", 900, 500)], moves)).toHaveLength(0);
  });
});

describe("a lane of evenly spaced commits drawn one commit early", () => {
  it("is moved onto all its commits (the centred drawing still had two of three on commits)", () => {
    const objects = [
      circle("shared_base", 570, 515, 19),
      circle("main_commit_1", 750, 515, 24),
      circle("main_commit_2", 930, 515, 24),
      {
        id: "main_trail",
        type: "path",
        params: { d: "M 0 0 L 180 0 L 360 0", closed: false },
        place: { x: 570, y: 515 },
      },
    ];
    const hits = compileScene(lifted({ objects })).issues.filter((i) => i.code === "PATH_OFF_ITS_POINTS");
    expect(hits).toHaveLength(1);
    expect(hits[0]?.fix).toMatchObject({ dx: 180, dy: 0 });
  });
});
