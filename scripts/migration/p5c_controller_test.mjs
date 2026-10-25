import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
const output = resolve("var/p5c/controller-test.mjs");
await build({
  entryPoints: ["tests/desktop_host/controller.test.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: output,
});
execFileSync(process.execPath, [output], { stdio: "inherit" });
