import { compileFromFile } from 'json-schema-to-typescript';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const result = await compileFromFile(path.join(root, 'packages/battle-protocol/src/ptcg_protocol/schema/scaffold.schema.json'), {
  bannerComment: '// Generated from scaffold.schema.json; build-time P1 only. Do not edit.',
  style: { singleQuote: false, semi: true, tabWidth: 2 },
});
const output = path.join(root, 'packages/battle-protocol/ts/src/scaffold.d.ts');
if (process.argv.includes('--check')) {
  if (await readFile(output, 'utf8') !== result) throw new Error('Generated TypeScript has drifted');
} else await writeFile(output, result);
