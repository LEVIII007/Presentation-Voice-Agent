import { defineConfig } from "vite";

const backendTarget = process.env.VITE_PROXY_TARGET || "http://localhost:7860";

export default defineConfig({
  // Honor the port injected by the tooling (falls back to 5173 for manual runs).
  server: {
    port: Number(process.env.PORT) || 5173,
    proxy: {
      "/api": { target: backendTarget, changeOrigin: true },
      "/health": { target: backendTarget, changeOrigin: true },
      "/connect": { target: backendTarget, changeOrigin: true },
      "/ws": { target: backendTarget, changeOrigin: true, ws: true },
    },
  },
});
