import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = { target: "http://localhost:8000", changeOrigin: true };
const apiPaths = [
  "/health",
  "/chat",
  "/requisitions",
  "/approvals",
  "/entities",
  "/graph",
  "/search",
  "/traces",
  "/evaluation",
];
const proxy = apiPaths.reduce<Record<string, typeof apiProxy>>((routes, path) => {
  routes[path] = apiProxy;
  return routes;
}, {});

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          cytoscape: ["cytoscape"],
        },
      },
    },
  },
  server: {
    proxy,
  },
});
