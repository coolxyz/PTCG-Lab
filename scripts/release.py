"""Build/verify a clean release, or prepare its pinned upstream image cache."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'__pycache__', 'node_modules', 'test-results', 'playwright-report', '.pytest_cache'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def release_files(root, release):
    files = set()
    def add(name):
        path = root / name
        if not path.exists():
            raise RuntimeError('Missing release dependency: ' + name)
        candidates = path.rglob('*') if path.is_dir() else [path]
        for p in candidates:
            if p.is_symlink():
                raise RuntimeError('Symlinks are not release inputs: ' + str(p))
            rel = p.relative_to(root)
            if p.is_file() and not SKIP.intersection(rel.parts) and not p.name.endswith(('.pyc', '.tsbuildinfo')):
                files.add(rel.as_posix())
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses', 'RELEASE.json', 'start.ps1', 'start.sh', '.gitignore', 'apps/api', 'apps/web/src', 'apps/web/dist', 'apps/web/e2e', 'packages', 'data', 'rulesets', 'scripts', 'tests', 'docs', 'runtime/engine', 'artifacts/engine/overlay-hashes.json'):
        add(name)
    for p in (root / 'apps/web').iterdir():
        if p.is_file() and not p.name.endswith('.tsbuildinfo'):
            add(p.relative_to(root).as_posix())
    rid = release['battleReleaseId']
    sha = release['upstreamCommit']
    if not re.fullmatch('[a-f0-9]{64}', rid) or not re.fullmatch('[a-f0-9]{40}', sha):
        raise RuntimeError('Invalid pinned release identifiers')
    add('.catalog/sync/releases/' + rid)
    add('.catalog/sync/snapshots/' + sha)
    return sorted(files)


def build(root=ROOT):
    release = read(root / 'RELEASE.json')
    version = release['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?', version):
        raise RuntimeError('Invalid version')
    # Fail before creating an output if any frozen battle dependency is corrupt.
    sys.path.insert(0, str(root))
    from packages.sync.releases import Releases
    Releases(root / '.catalog/sync', root=root).verify(release['battleReleaseId'])
    names = release_files(root, release)
    output = root / 'exports' / ('PTCG-Lab-' + version + '.tar.gz')
    output.parent.mkdir(exist_ok=True)
    hashes = {}
    pointer = json.dumps({'releaseId': release['battleReleaseId']}).encode()
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        def put(name, data):
            info = tarfile.TarInfo('PTCG-Lab/' + name)
            info.size = len(data)
            info.mode = 0o755 if name == 'start.sh' else 0o644
            archive.addfile(info, io.BytesIO(data))
            hashes[name] = hashlib.sha256(data).hexdigest()
        for name in names:
            put(name, (root / name).read_bytes())
        # New installs have no personal history or stale baseline references.
        put('.catalog/sync/current.json', pointer)
        put('.catalog/sync/baseline.json', pointer)
        put('RELEASE-FILES.json', json.dumps(hashes, sort_keys=True, indent=2).encode())
    with output.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    output.with_name(output.name + '.sha256').write_text(digest + '  ' + output.name + '\n', encoding='utf8')
    print(json.dumps({'archive': str(output), 'sha256': digest, 'files': len(hashes)-1, 'bytes': output.stat().st_size}))


def verify(root=ROOT):
    hashes = read(root / 'RELEASE-FILES.json')
    bad = []
    for name, expected in hashes.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            bad.append(name)
    if bad:
        raise RuntimeError('Missing/changed release files: ' + ', '.join(bad[:20]))
    print('Verified release files:', len(hashes))


def fetch_images():
    sys.path.insert(0, str(ROOT))
    from packages.sync.repositories import configured, source_for
    release = read(ROOT / 'RELEASE.json')
    source = source_for(ROOT, configured(ROOT / '.catalog/sync', ROOT))
    sha = release['upstreamCommit']
    source.validate_sha(sha)
    source.initialize()
    source.git('config', 'remote.origin.promisor', 'true')
    source.git('config', 'remote.origin.partialclonefilter', 'blob:none')
    source.git('fetch', '--filter=blob:none', '--no-tags', 'origin', sha, timeout=600)
    print('Pinned image source ready:', sha)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'verify', 'fetch-images'])
    parser.add_argument('--include-restricted-data', action='store_true', help='Build a private full-data package; does not grant redistribution rights')
    args = parser.parse_args()
    if args.command == 'build' and not args.include_restricted_data:
        parser.error('Full release includes restricted datasets. For GitHub use scripts/public/export_source.py; private full-data build requires --include-restricted-data.')
    try:
        {'build': build, 'verify': verify, 'fetch-images': fetch_images}[args.command]()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc))
