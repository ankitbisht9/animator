/**
 * TeX command allowlist. LLM-written TeX is untrusted: only these commands may
 * appear. Notably absent: \cssId \class \style \href \require \def \newcommand
 * \renewcommand \let \unicode \color \html (the engine adds \cssId itself).
 */

const GREEK = [
  "alpha",
  "beta",
  "gamma",
  "delta",
  "epsilon",
  "varepsilon",
  "zeta",
  "eta",
  "theta",
  "vartheta",
  "iota",
  "kappa",
  "lambda",
  "mu",
  "nu",
  "xi",
  "pi",
  "varpi",
  "rho",
  "varrho",
  "sigma",
  "varsigma",
  "tau",
  "upsilon",
  "phi",
  "varphi",
  "chi",
  "psi",
  "omega",
  "Gamma",
  "Delta",
  "Theta",
  "Lambda",
  "Xi",
  "Pi",
  "Sigma",
  "Upsilon",
  "Phi",
  "Psi",
  "Omega",
];

const COMMANDS = [
  ...GREEK,
  // structure
  "frac",
  "dfrac",
  "tfrac",
  "sqrt",
  "left",
  "right",
  "big",
  "Big",
  "bigg",
  "Bigg",
  "text",
  "textbf",
  "mathrm",
  "mathbf",
  "mathit",
  "mathsf",
  "mathbb",
  "operatorname",
  "displaystyle",
  "textstyle",
  "overline",
  "underline",
  "overbrace",
  "underbrace",
  "boxed",
  "hat",
  "vec",
  "bar",
  "dot",
  "ddot",
  "tilde",
  "begin",
  "end",
  // operators and relations
  "cdot",
  "times",
  "div",
  "pm",
  "mp",
  "approx",
  "neq",
  "ne",
  "leq",
  "le",
  "geq",
  "ge",
  "equiv",
  "propto",
  "sim",
  "ll",
  "gg",
  "to",
  "rightarrow",
  "leftarrow",
  "Rightarrow",
  "Leftarrow",
  "leftrightarrow",
  "Leftrightarrow",
  "uparrow",
  "downarrow",
  "infty",
  "partial",
  "nabla",
  "sum",
  "prod",
  "int",
  "lim",
  "sin",
  "cos",
  "tan",
  "log",
  "ln",
  "exp",
  "min",
  "max",
  "circ",
  "degree",
  "prime",
  "ldots",
  "cdots",
  "dots",
  "therefore",
  "because",
  "perp",
  "parallel",
  "angle",
  "triangle",
  "checkmark",
  // spacing
  "quad",
  "qquad",
];

const ALLOWED = new Set(COMMANDS);
/** Single-character control symbols: spacing and escaped specials. */
const ALLOWED_SYMBOLS = new Set([",", ";", ":", "!", " ", "{", "}", "%", "_", "#", "&", "$", "\\", "|"]);
const ALLOWED_ENVS = new Set(["aligned", "array", "matrix", "pmatrix", "bmatrix", "cases"]);

export const TEX_MAX_LENGTH = 400;

/** Returns a list of problems; empty means the TeX is acceptable. */
export function checkTex(tex: string): string[] {
  const problems: string[] = [];
  if (tex.length > TEX_MAX_LENGTH) problems.push(`TeX longer than ${TEX_MAX_LENGTH} characters`);
  for (const m of tex.matchAll(/\\([A-Za-z]+|.)/g)) {
    const name = m[1] ?? "";
    const ok = /^[A-Za-z]+$/.test(name) ? ALLOWED.has(name) : ALLOWED_SYMBOLS.has(name);
    if (!ok) problems.push(`TeX command \\${name} is not allowed`);
  }
  for (const m of tex.matchAll(/\\(?:begin|end)\{([^}]*)\}/g)) {
    if (!ALLOWED_ENVS.has(m[1] ?? "")) problems.push(`TeX environment '${m[1]}' is not allowed`);
  }
  let depth = 0;
  for (let i = 0; i < tex.length; i++) {
    if (tex[i] === "\\") {
      i++;
      continue;
    }
    if (tex[i] === "{") depth++;
    if (tex[i] === "}") depth--;
    if (depth < 0) break;
  }
  if (depth !== 0) problems.push("unbalanced braces in TeX");
  return problems;
}
