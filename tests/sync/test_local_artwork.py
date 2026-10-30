"""Local clones are read-only, pinned raster sources, never executable repositories."""
import io
import os
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from packages.sync.artwork import ArtworkCache, DEFAULTS, DownloadError
from packages.sync.common import SyncError
from packages.sync.http_source import object_sha
from packages.sync.local_artwork import LocalGitArtwork


def image():
    output = io.BytesIO()
    Image.new('RGB', (4, 6), 'purple').save(output, 'PNG')
    return output.getvalue()


@pytest.fixture
def clone(tmp_path):
    root = tmp_path.resolve() / 'clone'
    (root / '.git').mkdir(parents=True)
    (root / 'img').mkdir()
    raw = image()
    (root / 'img/one.png').write_bytes(raw)
    return root, raw, object_sha('blob', raw)


def test_direct_read_without_git_network_or_duplicate_cache(clone, tmp_path, monkeypatch):
    root, raw, blob = clone
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('No Git or subprocess'))
    cache = ArtworkCache(tmp_path.resolve() / 'profile', transport=lambda *a: pytest.fail('No network'))
    cache.configure({**DEFAULTS, 'source': 'local-git', 'localGitPath': str(root)})
    before = (root / 'img/one.png').read_bytes()
    assert cache.image('https://github.com/example/Cards', 'a'*40, 'img/one.png', blob) == (raw, 'image/png')
    assert cache.status()['cachedImages'] == 0 and cache.status()['localGitAvailable']
    assert set(p.name for p in cache.directory.iterdir()) == {'index.sqlite'}
    reopened = ArtworkCache(cache.home, transport=lambda *a: pytest.fail('No network'))
    assert reopened.image('https://github.com/example/Cards', 'a'*40, 'img/one.png', blob)[0] == raw
    assert (root / 'img/one.png').read_bytes() == before


def test_missing_mismatch_and_invalid_images_never_fall_back_to_network(clone, tmp_path):
    root, raw, blob = clone
    cache = ArtworkCache(tmp_path.resolve() / 'profile', transport=lambda *a: pytest.fail('No fallback network'))
    cache.configure({**DEFAULTS, 'source': 'local-git', 'localGitPath': str(root)})
    for path, expected in [('img/missing.png', blob), ('img/one.png', 'b'*40)]:
        with pytest.raises(SyncError): cache.image('https://github.com/example/Cards', 'a'*40, path, expected)
    (root / 'img/one.png').write_bytes(b'not an image')
    with pytest.raises(SyncError, match='完整图像校验'):
        LocalGitArtwork(str(root)).read('img/one.png', object_sha('blob', b'not an image'))
    assert cache.status()['cachedImages'] == 0


@pytest.mark.parametrize('path', ['../private.png','img/../private.png','/etc/passwd','img//one.png','.git/config','other/one.png','img/one.svg','img/one.png\n'])
def test_only_pinned_img_paths_are_read(clone, path):
    root, _, blob = clone
    with pytest.raises(SyncError): LocalGitArtwork(str(root)).read(path, blob)


def test_symlink_hardlink_and_fifo_are_rejected_without_blocking(clone, tmp_path):
    root, raw, blob = clone
    (root / 'img/link.png').symlink_to(root / 'img/one.png')
    with pytest.raises(SyncError): LocalGitArtwork(str(root)).read('img/link.png', blob)
    os.link(root / 'img/one.png', root / 'img/hard.png')
    with pytest.raises(SyncError): LocalGitArtwork(str(root)).read('img/hard.png', blob)
    if hasattr(os, 'mkfifo'):
        os.mkfifo(root / 'img/fifo.png')
        with pytest.raises(SyncError): LocalGitArtwork(str(root)).read('img/fifo.png', blob)
    alias = tmp_path.resolve() / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(SyncError): LocalGitArtwork(str(alias)).validate()


def test_source_disappearing_does_not_break_settings_or_delete_cache(clone, tmp_path):
    root, raw, blob = clone
    cache = ArtworkCache(tmp_path.resolve() / 'profile')
    cache.configure({**DEFAULTS, 'source':'local-git', 'localGitPath':str(root)})
    root.rename(root.with_name('moved'))
    assert cache.status()['localGitAvailable'] is False
    cache.configure(DEFAULTS)
    assert cache.status()['source'] == 'github-raw'


def test_switching_to_clone_ignores_online_throttle_and_keeps_verified_cache(clone, tmp_path):
    root,raw,blob=clone
    cache=ArtworkCache(tmp_path.resolve()/'profile',clock=lambda:100)
    cache.host_cooldown['images.example.org']=(220,DownloadError('IMAGE_SOURCE_BUSY','busy',120))
    cache.configure({**DEFAULTS,'source':'local-git','localGitPath':str(root),'baseUrl':'https://images.example.org'})
    assert cache.status()['retryAfterSeconds']==0
    cache._store(blob,raw,'image/png',1024**2)
    root.rename(root.with_name('moved'))
    assert cache.image('https://github.com/example/Cards','a'*40,'img/one.png',blob)[0]==raw
    assert cache.status()['cachedImages']==1


def test_unavailable_stored_path_remains_editable_but_cannot_be_saved(tmp_path):
    import json
    cache=ArtworkCache(tmp_path.resolve())
    cache.config.write_text(json.dumps({**DEFAULTS,'source':'local-git','localGitPath':'relative/invalid'}))
    assert cache.status()['localGitAvailable'] is False
    with pytest.raises(SyncError):cache.configure(cache.configuration())
    cache.configure(DEFAULTS)
    assert cache.configuration()['source']=='github-raw'


@pytest.mark.parametrize('path', ['', 'relative/path', '//host/share', r'\\host\share', '/tmp/../clone'])
def test_invalid_local_roots_do_not_replace_settings(tmp_path, path):
    cache = ArtworkCache(tmp_path.resolve())
    with pytest.raises(SyncError): cache.configure({**DEFAULTS, 'source':'local-git', 'localGitPath':path})
    assert cache.configuration() == DEFAULTS
