// Export the invented browser scenarios for the authoritative Python schema checker.
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
const entry = fileURLToPath(
  new URL("../../apps/desktop-web/src/demo-data.ts", import.meta.url),
);
const bundle = await build({
  entryPoints: [entry],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
});
const { demoSnapshot, demoReplay, scenarios } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const cases = ["PLAYER1", "PLAYER2"].flatMap((seat) => [
  ...scenarios.map((s) => ({
    kind: "MatchSnapshot",
    value: demoSnapshot(seat, s),
  })),
  { kind: "ReplayPage", value: demoReplay(seat) },
]);
process.stdout.write(JSON.stringify(cases));
