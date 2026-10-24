import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5183, strictPort: true },
  // One React instance for both workspace packages. No legacy API proxy.
  resolve: { dedupe: ["react", "react-dom"] },
});
