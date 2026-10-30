import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { workspaceAliases, workspaceEntries, workspaceSourceCheck } from "./workspace-source";
const root = fileURLToPath(new URL("../../apps/web/", import.meta.url));
export default defineConfig({
  root,
  plugins: [react(), workspaceSourceCheck()],
  resolve: {
    alias: [
      {
        find: "./Practice",
        replacement: workspaceEntries().get("@ptcg/desktop-web/practice")!,
      },
      ...workspaceAliases(),
    ],
    dedupe: ["react", "react-dom"],
  },
  build: { outDir: "dist", emptyOutDir: true },
});
