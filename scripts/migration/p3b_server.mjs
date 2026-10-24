// Test-only process supervisor. A staged baseline and a fresh DB are mandatory.
import { readFile, realpath } from "node:fs/promises";
import { resolve, join } from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
const stage = await realpath(
  process.env.PTCG_PRACTICE_TEST_STAGE ?? "missing-private-stage",
);
const manifest = JSON.parse(
  await readFile(join(stage, "baseline.json"), "utf8"),
);
if (!manifest.appStaged || manifest.userDatabasesCopied !== false)
  throw new Error("Not an isolated baseline");
const root = join(stage, "app");
const python = resolve(
  process.env.PTCG_PRACTICE_TEST_PYTHON ?? "missing-private-python",
);
const child = spawn(
  python,
  [
    "-m",
    "uvicorn",
    "apps.api.main:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    "8783",
  ],
  {
    cwd: root,
    stdio: "inherit",
    env: {
      ...process.env,
      PTCG_DB: join(stage, `acceptance-${randomUUID()}.sqlite`),
      PYTHONPATH: root,
    },
  },
);
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => child.kill(signal));
child.on("exit", (code) => process.exit(code ?? 1));
child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
