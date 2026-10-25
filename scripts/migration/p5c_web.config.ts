import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../../apps/desktop-web", import.meta.url));
export default defineConfig({
  root,
  plugins: [react()],
  resolve: { dedupe: ["react", "react-dom"] },
  build: {
    outDir: "../../var/p5c/web-dist",
    emptyOutDir: true,
    rollupOptions: { input: root + "/online.html" },
  },
});
