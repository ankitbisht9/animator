/**
 * Restricted expression language for bindings and computed values.
 *
 *   expr   := number | ident(.ident)* | expr op expr | -expr | fn(expr, ...) | (expr)
 *   op     := + - * / % ^
 *
 * A hand-written Pratt parser: no eval, no Function, no property access beyond
 * dotted identifiers that the caller resolves. Bounded length and depth.
 */

export type Expr =
  | { k: "num"; v: number }
  | { k: "ref"; name: string }
  | { k: "neg"; e: Expr }
  | { k: "bin"; op: "+" | "-" | "*" | "/" | "%" | "^"; l: Expr; r: Expr }
  | { k: "call"; fn: string; args: Expr[] };

export class ExprError extends Error {
  constructor(
    message: string,
    readonly pos: number,
  ) {
    super(message);
  }
}

export const EXPR_MAX_LENGTH = 240;
export const EXPR_MAX_DEPTH = 32;

type Fn = { arity: [number, number]; f: (...a: number[]) => number };

export const EXPR_FUNCTIONS: Record<string, Fn> = {
  sin: { arity: [1, 1], f: Math.sin },
  cos: { arity: [1, 1], f: Math.cos },
  tan: { arity: [1, 1], f: Math.tan },
  asin: { arity: [1, 1], f: Math.asin },
  acos: { arity: [1, 1], f: Math.acos },
  atan: { arity: [1, 1], f: Math.atan },
  atan2: { arity: [2, 2], f: Math.atan2 },
  sqrt: { arity: [1, 1], f: Math.sqrt },
  abs: { arity: [1, 1], f: Math.abs },
  exp: { arity: [1, 1], f: Math.exp },
  log: { arity: [1, 1], f: Math.log },
  log10: { arity: [1, 1], f: Math.log10 },
  floor: { arity: [1, 1], f: Math.floor },
  ceil: { arity: [1, 1], f: Math.ceil },
  round: { arity: [1, 1], f: Math.round },
  sign: { arity: [1, 1], f: Math.sign },
  min: { arity: [1, 8], f: Math.min },
  max: { arity: [1, 8], f: Math.max },
  pow: { arity: [2, 2], f: Math.pow },
  clamp: { arity: [3, 3], f: (v, lo, hi) => Math.min(hi, Math.max(lo, v)) },
  lerp: { arity: [3, 3], f: (a, b, u) => a + (b - a) * u },
  deg: { arity: [1, 1], f: (r) => (r * 180) / Math.PI },
  rad: { arity: [1, 1], f: (d) => (d * Math.PI) / 180 },
};

export const EXPR_CONSTANTS: Record<string, number> = { pi: Math.PI, e: Math.E };

type Tok =
  | { t: "num"; v: number; pos: number }
  | { t: "id"; v: string; pos: number }
  | { t: "op"; v: string; pos: number }
  | { t: "end"; pos: number };

const ID_START = /[A-Za-z_]/;
const ID_PART = /[A-Za-z0-9_]/;
const DIGIT = /[0-9]/;

function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i] as string;
    if (c === " " || c === "\t") {
      i++;
      continue;
    }
    if (DIGIT.test(c) || (c === "." && DIGIT.test(src[i + 1] ?? ""))) {
      const m = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(src.slice(i));
      if (!m) throw new ExprError("malformed number", i);
      out.push({ t: "num", v: Number(m[0]), pos: i });
      i += m[0].length;
      continue;
    }
    if (ID_START.test(c)) {
      const start = i;
      while (i < src.length) {
        while (i < src.length && ID_PART.test(src[i] as string)) i++;
        if (src[i] === "." && ID_START.test(src[i + 1] ?? "")) i++;
        else break;
      }
      out.push({ t: "id", v: src.slice(start, i), pos: start });
      continue;
    }
    if ("+-*/%^(),".includes(c)) {
      out.push({ t: "op", v: c, pos: i });
      i++;
      continue;
    }
    throw new ExprError(`unexpected character '${c}'`, i);
  }
  out.push({ t: "end", pos: src.length });
  return out;
}

const BINDING_POWER: Record<string, number> = { "+": 10, "-": 10, "*": 20, "/": 20, "%": 20, "^": 30 };

export function parseExpr(src: string): Expr {
  if (src.length > EXPR_MAX_LENGTH) throw new ExprError(`expression longer than ${EXPR_MAX_LENGTH} chars`, 0);
  const toks = tokenize(src);
  let p = 0;
  const peek = () => toks[p] as Tok;
  const next = () => toks[p++] as Tok;

  function expectOp(v: string) {
    const tk = next();
    if (tk.t !== "op" || tk.v !== v) throw new ExprError(`expected '${v}'`, tk.pos);
  }

  function prefix(depth: number): Expr {
    if (depth > EXPR_MAX_DEPTH) throw new ExprError("expression nested too deeply", peek().pos);
    const tk = next();
    if (tk.t === "num") return { k: "num", v: tk.v };
    if (tk.t === "id") {
      if (peek().t === "op" && (peek() as { v: string }).v === "(") {
        next();
        const fn = EXPR_FUNCTIONS[tk.v];
        if (!fn || !Object.hasOwn(EXPR_FUNCTIONS, tk.v)) throw new ExprError(`unknown function '${tk.v}'`, tk.pos);
        const args: Expr[] = [];
        if (!(peek().t === "op" && (peek() as { v: string }).v === ")")) {
          for (;;) {
            args.push(parse(0, depth + 1));
            const sep = peek();
            if (sep.t === "op" && sep.v === ",") {
              next();
              continue;
            }
            break;
          }
        }
        expectOp(")");
        if (args.length < fn.arity[0] || args.length > fn.arity[1]) {
          throw new ExprError(`'${tk.v}' takes ${fn.arity.join("-")} arguments, got ${args.length}`, tk.pos);
        }
        return { k: "call", fn: tk.v, args };
      }
      return { k: "ref", name: tk.v };
    }
    if (tk.t === "op" && tk.v === "-") return { k: "neg", e: parse(25, depth + 1) };
    if (tk.t === "op" && tk.v === "+") return parse(25, depth + 1);
    if (tk.t === "op" && tk.v === "(") {
      const e = parse(0, depth + 1);
      expectOp(")");
      return e;
    }
    throw new ExprError(tk.t === "end" ? "unexpected end of expression" : `unexpected '${tk.v}'`, tk.pos);
  }

  function parse(minBp: number, depth: number): Expr {
    let left = prefix(depth);
    for (;;) {
      const tk = peek();
      if (tk.t !== "op") break;
      const bp = BINDING_POWER[tk.v];
      if (bp === undefined || bp <= minBp) break;
      next();
      // '^' is right-associative.
      const right = parse(tk.v === "^" ? bp - 1 : bp, depth + 1);
      left = { k: "bin", op: tk.v as "+", l: left, r: right };
    }
    return left;
  }

  const ast = parse(0, 0);
  const end = peek();
  if (end.t !== "end") throw new ExprError(`unexpected '${"v" in end ? end.v : ""}'`, end.pos);
  return ast;
}

/** All identifiers an expression references (excluding built-in constants). */
export function exprRefs(e: Expr, out: Set<string> = new Set()): Set<string> {
  switch (e.k) {
    case "ref":
      if (!Object.hasOwn(EXPR_CONSTANTS, e.name)) out.add(e.name);
      break;
    case "neg":
      exprRefs(e.e, out);
      break;
    case "bin":
      exprRefs(e.l, out);
      exprRefs(e.r, out);
      break;
    case "call":
      for (const a of e.args) exprRefs(a, out);
      break;
  }
  return out;
}

export type Resolver = (name: string) => number | undefined;

export function evalExpr(e: Expr, resolve: Resolver): number {
  switch (e.k) {
    case "num":
      return e.v;
    case "ref": {
      if (Object.hasOwn(EXPR_CONSTANTS, e.name)) return EXPR_CONSTANTS[e.name] as number;
      const v = resolve(e.name);
      if (v === undefined) throw new ExprError(`unknown identifier '${e.name}'`, 0);
      return v;
    }
    case "neg":
      return -evalExpr(e.e, resolve);
    case "bin": {
      const l = evalExpr(e.l, resolve);
      const r = evalExpr(e.r, resolve);
      switch (e.op) {
        case "+":
          return l + r;
        case "-":
          return l - r;
        case "*":
          return l * r;
        case "/":
          return l / r;
        case "%":
          return l % r;
        case "^":
          return l ** r;
      }
      break;
    }
    case "call": {
      const fn = EXPR_FUNCTIONS[e.fn] as Fn;
      return fn.f(...e.args.map((a) => evalExpr(a, resolve)));
    }
  }
  return Number.NaN;
}
