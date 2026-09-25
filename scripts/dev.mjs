// `make dev`: run the Vite dev server and the FastAPI app side by side with
// prefixed output. Ctrl+C stops both.
import { spawn } from "node:child_process";

const procs = [
  ["web", "pnpm", ["--filter", "@animator/web", "dev"]],
  [
    "api",
    "uv",
    [
      "run",
      "uvicorn",
      "animator.api.app:app",
      "--reload",
      "--reload-dir",
      "apps/api/src",
      "--port",
      process.env.API_PORT ?? "8000",
    ],
  ],
];

const children = procs.map(([name, cmd, args]) => {
  const child = spawn(cmd, args, { shell: process.platform === "win32", stdio: ["ignore", "pipe", "pipe"] });
  const tag = (chunk) =>
    chunk
      .toString()
      .split(/\r?\n/)
      .filter(Boolean)
      .map((l) => `[${name}] ${l}`)
      .join("\n");
  child.stdout.on("data", (c) => console.log(tag(c)));
  child.stderr.on("data", (c) => console.error(tag(c)));
  child.on("exit", (code) => {
    console.log(`[${name}] exited with ${code}`);
    for (const other of children) if (other !== child) other.kill();
    process.exit(code ?? 0);
  });
  return child;
});

process.on("SIGINT", () => {
  for (const c of children) c.kill();
});
