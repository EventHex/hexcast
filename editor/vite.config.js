import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI at /editor (see webstudio/app.py). `npm run dev` proxies
// API/media calls to the running studio server for live development.
export default defineConfig({
  base: "/editor/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/media": "http://127.0.0.1:8765",
      "/assets": "http://127.0.0.1:8765",
    },
  },
});
