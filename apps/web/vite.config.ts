import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const repoRoot = resolve(__dirname, "../..");

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.WEB_DEV_PORT ?? 5173),
    fs: { allow: [repoRoot] },
    proxy: { "/api": `http://127.0.0.1:${process.env.API_PORT ?? 8000}` },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 4096,
  },
});
