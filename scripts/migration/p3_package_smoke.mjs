/** Consume inspected npm archives outside the checkout using the pinned build tools.
 * React/icon dependencies reuse the installed toolchain; workspace source aliases
 * are deliberately absent. This is package portability, not an offline installer.
 */
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  symlink,
  rm,
  readdir,
  realpath,
} from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const artifacts = resolve(process.argv[2] ?? "var/p3/build-01");
const temp = await realpath(await mkdtemp(join(tmpdir(), "ptcg-p3-consumer-")));
try {
  for (const [folder, name] of [
    ["packages/battle-ui", "battle-ui"],
    ["apps/desktop-web", "desktop-web"],
    ["packages/battle-protocol/ts", "battle-protocol"],
  ]) {
    const metadata = JSON.parse(
      await readFile(join(root, folder, "package.json"), "utf8"),
    );
    const archive = join(artifacts, `ptcg-${name}-${metadata.version}.tgz`);
    const target = join(temp, "node_modules/@ptcg", name);
    await mkdir(target, { recursive: true });
    execFileSync("tar", [
      "-xzf",
      archive,
      "-C",
      target,
      "--strip-components=1",
    ]);
  }
  for (const dependency of ["react", "react-dom", "lucide-react"]) {
    await symlink(
      join(root, "node_modules", dependency),
      join(temp, "node_modules", dependency),
      "dir",
    );
  }
  await writeFile(
    join(temp, "package.json"),
    JSON.stringify({ private: true, type: "module" }),
  );
  await writeFile(
    join(temp, "index.html"),
    '<html><body><div id="root"></div><script type="module" src="/main.tsx"></script></body></html>',
  );
  await writeFile(
    join(temp, "cases.json"),
    execFileSync(process.execPath, [
      join(root, "scripts/migration/p3_fixture_export.mjs"),
    ]),
  );
  await writeFile(
    join(temp, "main.tsx"),
    `import {createRoot} from "react-dom/client";
import {BattleArena} from "@ptcg/battle-ui";
import {snapshotView} from "@ptcg/desktop-web";
import cases from "./cases.json";
createRoot(document.getElementById("root")).render(<BattleArena view={snapshotView(cases[0].value)} cards={{}} onRefresh={()=>{}} onLeave={()=>{}}/>);`,
  );
  await build({
    root: temp,
    configFile: false,
    esbuild: { jsx: "automatic" },
    resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "warn",
  });
  const files = await readdir(join(temp, "dist/assets"));
  if (
    !files.some((n) => n.endsWith(".css")) ||
    !files.some((n) => n.endsWith(".js"))
  )
    throw new Error("Missing standalone display assets");
  const report = {
    status: "passed",
    npmArchives: 3,
    workspaceSourceAliases: false,
    reusedPinnedReactToolchain: true,
    outsideCheckout: true,
    assets: files,
  };
  await writeFile(
    join(artifacts, "P3-CONSUMER-REPORT.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} finally {
  await rm(temp, { recursive: true, force: true });
}
