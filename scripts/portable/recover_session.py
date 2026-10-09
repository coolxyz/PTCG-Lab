"""Local owner recovery. Rotates one session identity, retaining match bodies."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import secrets
import sqlite3
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[2]


def rotate(db_path, owner, token):
    new_owner = hashlib.sha256(token.encode()).hexdigest()
    with sqlite3.connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM battle_sessions WHERE owner=?', (owner,)).fetchone():
            raise ValueError('Unknown owner')
        db.execute('INSERT INTO battle_sessions SELECT ?,created_at FROM battle_sessions WHERE owner=?', (new_owner, owner))
        db.execute('UPDATE matches SET owner=? WHERE owner=?', (new_owner, owner))
        db.execute('DELETE FROM battle_sessions WHERE owner=?', (owner,))
    return new_owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', help='Owner from --list; required when multiple owners have matches')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--db', type=Path, default=ROOT / 'var/app.sqlite')
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit('Database not found')
    with sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True) as db:
        owners = db.execute('SELECT owner,COUNT(*) FROM matches GROUP BY owner').fetchall()
    if args.list:
        for owner, count in owners:
            print(owner, count, 'matches')
        return
    owner = args.owner or (owners[0][0] if len(owners) == 1 else None)
    if owner not in {row[0] for row in owners}:
        raise SystemExit('Run --list, then select --owner. No records changed.')
    token, path = secrets.token_urlsafe(32), '/' + secrets.token_urlsafe(32)
    complete = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            nonlocal complete
            if self.path != path or complete:
                self.send_error(404)
                return
            rotate(args.db, owner, token)
            complete = True
            self.send_response(302)
            self.send_header('Set-Cookie', f'ptcg_practice_session={token}; HttpOnly; SameSite=Strict; Path=/api/battle; Max-Age=31536000')
            self.send_header('Location', 'http://127.0.0.1:8765/#battle')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()

    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        server.timeout = 1
        url = f'http://127.0.0.1:{server.server_port}{path}'
        print('Open locally within 5 minutes (private, one-time recovery URL):', url, flush=True)
        webbrowser.open(url)
        deadline = time.monotonic() + 300
        while not complete and time.monotonic() < deadline:
            server.handle_request()
    print('Session restored; old browser session invalidated.' if complete else 'Timed out; no records changed.')


if __name__ == '__main__':
    main()
