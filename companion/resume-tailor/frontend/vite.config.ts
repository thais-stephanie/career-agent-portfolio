// Modified for the Career Agent public edition (2026-09-22). See NOTICE.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Built assets are served by FastAPI (src/resume_tailor/ui/static_v2): end users never
// need Node. Node is a contributor/build dependency only.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: resolve(__dirname, "../src/resume_tailor/ui/static_v2"),
    emptyOutDir: true,
    rollupOptions: { output: { banner: "/*! Modified for the Career Agent public edition (2026-09-22). See NOTICE. Apache-2.0. */" } },
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
