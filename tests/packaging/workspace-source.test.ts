import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { workspaceAliases, workspaceEntries, workspaceSourceCheck } from "../../scripts/product/workspace-source";

const root = process.cwd();
const entries = workspaceEntries(root);
assert(entries.get("@ptcg/desktop-web/online")!.startsWith(root + "/apps/desktop-web/src/"));
assert(entries.get("@ptcg/desktop-web/practice")!.startsWith(root + "/apps/desktop-web/src/"));
for (const [name, target] of entries) {
  assert(target.startsWith(root + "/"));
  assert.equal(workspaceAliases(root).filter(a => a.find.test(name)).length, 1);
}
let emitted: any;
const plugin = workspaceSourceCheck(root);
const generate = plugin.generateBundle as Function;
generate.call({getModuleIds:()=>[entries.get("@ptcg/desktop-web/online")], emitFile:(v: any)=>emitted=v});
assert.equal(JSON.parse(emitted.source).modules[0].path, "apps/desktop-web/src/online/index.ts");
assert.throws(()=>generate.call({getModuleIds:()=>["/foreign/apps/desktop-web/src/online/index.ts"],emitFile:()=>{}}), /Foreign workspace/);
assert.throws(()=>generate.call({getModuleIds:()=>[],emitFile:()=>{}}), /lacks current workspace/);

const temp=mkdtempSync(resolve(tmpdir(),"ptcg-workspace-contract-"));
try {
  for(const [name, dir] of [["@ptcg/desktop-web","apps/desktop-web"],["@ptcg/battle-ui","packages/battle-ui"],["@ptcg/battle-protocol","packages/battle-protocol/ts"],["@ptcg/platform-services","packages/platform-services"]]) {
    const pkg=resolve(temp,dir);mkdirSync(resolve(pkg,"src"),{recursive:true});
    writeFileSync(resolve(pkg,"package.json"),JSON.stringify({name,exports:{".":"./src/index.ts"}}));
    writeFileSync(resolve(pkg,"src/index.ts"),"export {};");
  }
  assert.equal(workspaceEntries(temp).size,4);
  const link=resolve(temp,"apps/desktop-web/src/index.ts");rmSync(link);
  // A package export may not escape its own checkout through a filesystem link.
  const external=mkdtempSync(resolve(tmpdir(),"ptcg-foreign-contract-"));
  try {
    writeFileSync(resolve(external,"index.ts"),"export {};");symlinkSync(resolve(external,"index.ts"),link);
    assert.throws(()=>workspaceEntries(temp),/escapes checkout/);
  } finally {rmSync(external,{recursive:true,force:true});}
} finally {rmSync(temp,{recursive:true,force:true});}
console.log("Current-workspace public exports, foreign modules and symlink refusal passed");
