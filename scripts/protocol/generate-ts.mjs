import { compileFromFile } from 'json-schema-to-typescript';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const meta = JSON.parse(await readFile(path.join(root, 'node_modules/json-schema-to-typescript/package.json'), 'utf8'));
if (meta.version !== '15.0.4') throw new Error('Install the pinned root package-lock.json');
for (const name of ['scaffold', 'v2']) {
  const result = await compileFromFile(path.join(root, `packages/battle-protocol/src/ptcg_protocol/schema/${name}.schema.json`), {
    bannerComment: name === 'scaffold'
      ? '// Generated from scaffold.schema.json; build-time P1 only. Do not edit.'
      : '// Generated from v2.schema.json. Do not edit. Runtime validation is required.',
    style: { singleQuote: false, semi: true, tabWidth: 2 },
    // Avoid enormous tuple unions for bounded collections. Runtime schema enforces bounds.
    maxItems: name === 'v2' ? 0 : 20,
  });
  const output = path.join(root, `packages/battle-protocol/ts/src/${name}.d.ts`);
  if (process.argv.includes('--check')) {
    if (await readFile(output, 'utf8') !== result) throw new Error(`Generated TypeScript has drifted: ${name}`);
  } else await writeFile(output, result);
}
