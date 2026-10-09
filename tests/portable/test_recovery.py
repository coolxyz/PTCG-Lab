import hashlib
import sqlite3
import pytest
from scripts.portable.recover_session import rotate


def test_rotation_preserves_match_payload_and_other_owner(tmp_path):
    path = tmp_path / 'state.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE battle_sessions(owner TEXT PRIMARY KEY,created_at TEXT); CREATE TABLE matches(id TEXT PRIMARY KEY,owner TEXT,body TEXT);')
        db.executemany('INSERT INTO battle_sessions VALUES(?,?)', [('old', 'date'), ('other', 'date')])
        db.executemany('INSERT INTO matches VALUES(?,?,?)', [('m1', 'old', '{"commands":[1,2]}'), ('m2', 'other', 'unchanged')])
    new = rotate(path, 'old', 'test-token')
    assert new == hashlib.sha256(b'test-token').hexdigest()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM matches ORDER BY id').fetchall() == [('m1', new, '{"commands":[1,2]}'), ('m2', 'other', 'unchanged')]
        assert db.execute('SELECT owner FROM battle_sessions WHERE owner=?', ('old',)).fetchone() is None
    with pytest.raises(ValueError):
        rotate(path, 'old', 'unused')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM battle_sessions').fetchone()[0] == 2
