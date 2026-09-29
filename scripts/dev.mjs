// `make dev`: run the Vite dev server and the FastAPI app side by side with
// prefixed output. Ctrl+C stops both.
import { spawn } from "node:child_process";
import net from "node:net";

/** Whether something already answers on 127.0.0.1:port. On Windows uvicorn can bind a port another
 * program is using (SO_REUSEADDR) and then never see a request, so "it started" proves nothing. */
const inUse = (port) =>
  new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    const done = (busy) => {
      socket.destroy();
      resolve(busy);
    };
    socket.setTimeout(500, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });

async function freeApiPort(wanted) {
  for (let port = wanted; port < wanted + 20; port++) {
    if (!(await inUse(port))) {
      if (port !== wanted) {
        console.log(`[dev] Port ${wanted} is already used by another program: the API runs on ${port} instead.`);
      }
      return port;
    }
  }
  throw new Error(`No free port for the API between ${wanted} and ${wanted + 19}.`);
}

const apiPort = String(await freeApiPort(Number(process.env.API_PORT ?? 8000)));
// The web dev server proxies /api to API_PORT (apps/web/vite.config.ts): both get the same port.
const env = { ...process.env, API_PORT: apiPort };

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
      // The engine worker is a subprocess; keep a loop that can spawn one (see animator/api/loop.py).
      "--loop",
      "animator.api.loop:loop_factory",
      // Open event streams (a lesson page left open) would otherwise block a reload forever and
      // leave the old code serving; running generations are paused and resume on restart.
      "--timeout-graceful-shutdown",
      "3",
      "--port",
      apiPort,
    ],
  ],
];

const children = procs.map(([name, cmd, args]) => {
  const child = spawn(cmd, args, { shell: process.platform === "win32", stdio: ["ignore", "pipe", "pipe"], env });
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
