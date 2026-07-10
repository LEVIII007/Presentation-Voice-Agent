import { defineConfig } from "vite";

export default defineConfig({
  // Honor the port injected by the tooling (falls back to 5173 for manual runs).
  server: { port: Number(process.env.PORT) || 5173 },
});
