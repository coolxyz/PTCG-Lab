"""Create a portable source/state snapshot. Stop the service before running."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
SKIP = {'venv', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache', '.DS_Store', 'test-results', 'playwright-report'}
CACHE = {'var/cache', 'var/uv-cache', 'var/npm-cache', 'exports'}


def excluded(path):
    rel = path.relative_to(ROOT)
    return (any(p in SKIP for p in rel.parts)
            or any(rel.as_posix() == c or rel.as_posix().startswith(c + '/') for c in CACHE)
            or path.name.endswith(('.pyc', '.tsbuildinfo', '-wal', '-shm', '-journal')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Archive outside the project directory')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        raise SystemExit('Output must be outside the project to prevent recursive archives.')
    if output.exists():
        raise SystemExit('Output exists; choose a new filename.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptcg-package-') as temp:
        stage = Path(temp) / 'PTCG-Lab'
        def copy_tree(src, dst):
            dst.mkdir(parents=True, exist_ok=True)
            for path in src.iterdir():
                if excluded(path):
                    continue
                target = dst / path.name
                if path.is_symlink():
                    raise RuntimeError(f'Unexpected symlink; inspect before packaging: {path}')
                if path.is_dir():
                    copy_tree(path, target)
                elif path.suffix == '.sqlite' and not path.relative_to(ROOT).as_posix().startswith('.catalog/sync/releases/'):
                    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(target) as dest:
                        source.backup(dest)
                        if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise RuntimeError(f'Corrupt database: {path}')
                else:
                    shutil.copy2(path, target)
        copy_tree(ROOT, stage)
        metadata = {
            'createdUtc': datetime.now(timezone.utc).isoformat(),
            'gitHead': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'gitStatus': subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True),
            'excludedRebuildable': sorted(SKIP | CACHE),
            'notes': 'Includes .git, uncommitted files, upstreams, generated overlay, all SQLite snapshots and art caches. Reinstall native dependencies on destination. Use local session recovery for previous matches.',
        }
        (stage / 'MIGRATION.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        hashes = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.rglob('*')) if p.is_file() and p.name != 'TRANSFER-MANIFEST.json'}
        (stage / 'TRANSFER-MANIFEST.json').write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding='utf-8')
        with tarfile.open(output, 'w:gz') as archive:
            archive.add(stage, arcname='PTCG-Lab')
    digest = hashlib.file_digest(output.open('rb'), 'sha256').hexdigest()
    output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n')
    print(json.dumps({'archive': str(output), 'sha256': digest, 'bytes': output.stat().st_size, 'files': len(hashes)}, indent=2))


if __name__ == '__main__':
    main()
