import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const api = process.env.PTCG_PRACTICE_API;
if (api) {
  const match = /^http:\/\/127\.0\.0\.1:(\d+)$/.exec(api);
  if (
    !match ||
    Number(match[1]) < 1024 ||
    Number(match[1]) > 65535 ||
    [8765, 8770, 8443].includes(Number(match[1]))
  )
    throw new Error(
      "Practice migration requires an explicit isolated loopback API port",
    );
}
export default defineConfig({
  plugins: [react()],
  define: {
    __PRACTICE_ENABLED__: JSON.stringify(
      !!api || process.env.PTCG_PRACTICE_MODE === "1",
    ),
  },
  server: {
    host: "127.0.0.1",
    port: 5183,
    strictPort: true,
    proxy: api
      ? Object.fromEntries(
          ["/api", "/card-images", "/uploaded-card-images"].map((prefix) => [
            prefix,
            { target: api },
          ]),
        )
      : undefined,
  },
  resolve: { dedupe: ["react", "react-dom"] },
});
