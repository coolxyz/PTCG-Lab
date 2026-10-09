"""Check extracted files and local SQLite state; no third-party dependencies."""
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]


def main():
    hashes = json.loads((ROOT / 'TRANSFER-MANIFEST.json').read_text(encoding='utf-8'))
    bad = [name for name, sha in hashes.items() if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha]
    if bad:
        raise SystemExit('Missing/changed files: ' + ', '.join(bad[:20]))
    with sqlite3.connect((ROOT / 'var/app.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    print(json.dumps({'verifiedFiles': len(hashes), 'database': 'ok', 'tableCounts': counts}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
