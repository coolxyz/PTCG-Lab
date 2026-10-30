import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { workspaceAliases, workspaceSourceCheck } from "../../scripts/product/workspace-source";
export default defineConfig({
  plugins: [react(), workspaceSourceCheck()],
  resolve: { alias: workspaceAliases(), dedupe: ["react", "react-dom"] },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
