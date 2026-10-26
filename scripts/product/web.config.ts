import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("../../apps/web/", import.meta.url));
export default defineConfig({
  root,
  plugins: [react()],
  resolve: {
    alias: [
      {
        find: "./Practice",
        replacement: require.resolve("@ptcg/desktop-web/practice"),
      },
    ],
    dedupe: ["react", "react-dom"],
  },
  build: { outDir: "dist", emptyOutDir: true },
});
