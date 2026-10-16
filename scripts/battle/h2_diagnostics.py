"""Read-only publish diagnostics; never rewrite effect bindings or frozen releases."""
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def collect(root):
    root = Path(root)
    paths = sorted((root / 'packages/rules').glob('*.py'))
    paths += [root / p for p in (
        'packages/engine_adapter/base.py', 'packages/engine_adapter/cn_format.py',
        'scripts/engine/build_engine.py', 'artifacts/engine/overlay-hashes.json',
        'data/engine/catalog.json', 'rulesets/cn-standard-2026-09-16.json',
        'data/cardpool/plain-pokemon.json')]
    contents, components = {}, {}
    for path in paths:
        raw = path.read_bytes()
        text = path.read_text(encoding='utf-8')
        name = path.relative_to(root).as_posix()
        contents[name] = text
        components[name] = {
            'textSha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'rawSha256': hashlib.sha256(raw).hexdigest(),
            'crlf': raw.count(b'\r\n'),
        }
    version = hashlib.sha256(json.dumps(contents, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    effects = json.loads((root / 'data/simulation/effects.json').read_text(encoding='utf-8'))
    overlay = json.loads(contents['artifacts/engine/overlay-hashes.json'])
    bad = []
    crlf = 0
    for name, expected in overlay.items():
        path = root / 'runtime/engine' / name
        raw = path.read_bytes() if path.is_file() else None
        if raw is None or hashlib.sha256(raw).hexdigest() != expected:
            bad.append(name)
        if raw is not None and b'\r\n' in raw:
            crlf += 1
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    return {
        'schemaVersion': 1, 'scope': 'workspace; active archived releases are not changed',
        'gitHead': head, 'platform': platform.system(), 'python': platform.python_version(),
        'runtimeEngineVersion': version, 'effectsEngineVersion': effects.get('engineVersion'),
        'engineMatchesEffects': version == effects.get('engineVersion'),
        'overlayMismatches': bad, 'overlayFilesWithCRLF': crlf,
        'components': components,
    }


def smoke_error(reply, endpoint, release_id, log_path):
    raw = reply.get('body', b'')
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        body = {'message': str(raw)[:2000]}
    if not isinstance(body, dict):
        body = {'message': str(body)[:2000]}
    # Include only useful error fields, not complete responses or request cookies.
    detail = {k: body[k] for k in ('code', 'message', 'detail') if k in body}
    return {'releaseId': release_id, 'endpoint': endpoint, 'status': reply['status'],
            'error': detail, 'workerLog': str(log_path)}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    report = collect(args.root)
    target = args.root / '.catalog/sync/h2-diagnostics.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k!='components'}, ensure_ascii=True, indent=2))
    print('Report: '+str(target.resolve()))
