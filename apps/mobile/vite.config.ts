import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import {
  workspaceAliases,
  workspaceSourceCheck,
} from "../../scripts/product/workspace-source";

export default defineConfig({
  plugins: [react(), workspaceSourceCheck()],
  resolve: { alias: workspaceAliases(), dedupe: ["react", "react-dom"] },
  clearScreen: false,
  build: { target: "es2022", emptyOutDir: true },
});
