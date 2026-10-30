/** Resolve this checkout's declared public exports, even with shared dependencies. */
import { readFileSync, realpathSync } from "node:fs";
import { createHash } from "node:crypto";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

const checkout = fileURLToPath(new URL("../../", import.meta.url));
const projects = [
  ["@ptcg/desktop-web", "apps/desktop-web"],
  ["@ptcg/battle-ui", "packages/battle-ui"],
  ["@ptcg/battle-protocol", "packages/battle-protocol/ts"],
  ["@ptcg/platform-services", "packages/platform-services"],
] as const;
const outside = (root: string, target: string) => {
  const path = relative(root, target);
  return path === ".." || path.startsWith(".." + sep) || isAbsolute(path);
};

export function workspaceEntries(root = checkout) {
  root = realpathSync(root);
  const entries = new Map<string, string>();
  for (const [name, directory] of projects) {
    const manifest = JSON.parse(readFileSync(resolve(root, directory, "package.json"), "utf8"));
    if (manifest.name !== name) throw new Error("Workspace package identity differs");
    const exports = manifest.exports;
    const rows = typeof exports === "object" && Object.keys(exports).some(k => k.startsWith("."))
      ? Object.entries(exports) : [[".", exports]];
    for (const [key, definition] of rows) {
      const target = typeof definition === "string" ? definition : (definition as {default?: string})?.default;
      if (!target?.startsWith("./src/")) throw new Error("Workspace public export must name local source");
      const file = realpathSync(resolve(root, directory, target));
      if (outside(root, file)) throw new Error("Workspace source escapes checkout");
      entries.set(key === "." ? name : name + key.slice(1), file);
    }
  }
  return entries;
}

export function workspaceAliases(root = checkout) {
  return [...workspaceEntries(root)].map(([name, replacement]) => ({
    find: new RegExp("^" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"), replacement,
  }));
}

export function workspaceSourceCheck(root = checkout): Plugin {
  root = realpathSync(root);
  return {
    name: "ptcg-current-workspace-source",
    generateBundle() {
      const modules: {path: string; sha256: string}[] = [];
      for (const id of this.getModuleIds()) {
        const file = id.split("?")[0];
        if (!projects.some(([, dir]) => file.includes("/" + dir + "/"))) continue;
        if (!isAbsolute(file) || outside(root, file)) throw new Error("Foreign workspace module in desktop build");
        modules.push({path: relative(root, file).split(sep).join("/"), sha256: createHash("sha256").update(readFileSync(file)).digest("hex")});
      }
      if (!modules.length) throw new Error("Desktop build lacks current workspace modules");
      modules.sort((a,b) => a.path.localeCompare(b.path));
      this.emitFile({type:"asset", fileName:"workspace-source.json", source:JSON.stringify({schema:1, modules}) + "\n"});
    },
  };
}
