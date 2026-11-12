import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on Vite's port and talks to `ddt serve` on 8787.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
