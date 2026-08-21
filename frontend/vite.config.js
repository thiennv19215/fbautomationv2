import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  build: {
    outDir: resolve(import.meta.dirname, "../fbem/bridge/static"),
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:47102" },
  },
});
