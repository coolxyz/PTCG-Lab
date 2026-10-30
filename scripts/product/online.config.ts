import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { workspaceAliases, workspaceSourceCheck } from "./workspace-source";
const root = fileURLToPath(new URL("../../apps/desktop-web", import.meta.url));
export default defineConfig({
  root,
  plugins: [react(), workspaceSourceCheck()],
  resolve: { alias: workspaceAliases(), dedupe: ["react", "react-dom"] },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: { input: root + "/online.html" },
  },
});
