"""Product-owned adapters. SQLite and media never enter the native client package."""

import json
import sqlite3
import re
from pathlib import Path
from hashlib import sha256
from threading import RLock
from ptcg_desktop_api.bridge import BridgeError, fields
from ptcg_desktop_api.resources import MAX_IMAGE, raster
from ptcg_online_client.contracts import ClientError
from ptcg_engine_provider.catalog import MemoryCatalog
from packages.collection.domain import RAW, ROOT, DomainError, content_hash, validation
from packages.rules.catalog import select_image


class ProductOnline:
    def __init__(self, store, media, sync_images=None):
        self.store, self.media, self.sync_images = store, media, sync_images
        with store.db() as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='online_deck_requests'"
            ).fetchone()
            if not exists:
                # SQLite backup includes committed WAL data. Never replace an earlier backup.
                backup = Path(store.path + ".before-product-online.sqlite")
                if backup.exists():
                    raise RuntimeError(
                        "Product migration backup already exists; inspect before retry"
                    )
                with sqlite3.connect(backup) as target:
                    db.backup(target)
                backup.chmod(0o600)
        with store.db(True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS online_deck_requests(
                server TEXT NOT NULL, identity TEXT NOT NULL, request TEXT NOT NULL,
                rule TEXT NOT NULL, content_hash TEXT NOT NULL, revision TEXT NOT NULL,
                entries TEXT NOT NULL, result TEXT,
                PRIMARY KEY(server,identity,request))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS online_deck_content ON online_deck_requests(server,identity,rule,content_hash)"
            )

    def resources(self, fallback):
        return ProductResources(self.media, fallback, self.sync_images)

    def pending(self, profile):
        identity = profile.client.status()["identity"]
        if not identity:
            return []
        owner = content_hash(
            {k: identity[k] for k in ("serverId", "userId", "deviceId")}
        )
        with self.store.db() as db:
            return [
                {
                    "revisionId": r["revision"],
                    "ruleReleaseId": r["rule"],
                    "requestId": r["request"],
                }
                for r in db.execute(
                    "SELECT * FROM online_deck_requests WHERE server=? AND identity=? AND result IS NULL",
                    (profile.server.key, owner),
                )
            ]

    def call(self, profile, action, body):
        try:
            return self._call(profile, action, body)
        except DomainError as error:
            raise BridgeError(error.code, error.status) from None

    def _call(self, profile, action, body):
        if action == "product-decks":
            fields(body)
            return {
                "decks": [
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "version": d["version"],
                        "revisions": [
                            {
                                "id": r["id"],
                                "number": r["number"],
                                "count": sum(e["quantity"] for e in r["entries"]),
                            }
                            for r in self.store.revisions(d["id"])
                        ],
                    }
                    for d in self.store.decks()
                ]
            }
        if action == "product-check":
            fields(body, ("revisionId",))
            if not isinstance(body["revisionId"], str):
                raise BridgeError("INVALID_REQUEST")
            with self.store.db() as db:
                row = db.execute(
                    "SELECT * FROM revisions WHERE id=? AND id NOT IN (SELECT id FROM deleted_revisions)",
                    (body["revisionId"],),
                ).fetchone()
            if row is None:
                raise BridgeError("REVISION_NOT_FOUND", 404)
            snapshot = json.loads(row["body"])
            if content_hash(snapshot) != row["hash"]:
                raise BridgeError("REVISION_CORRUPT", 409)
            return validation(snapshot["entries"])
        if action == "product-freeze":
            fields(body, ("deckId", "expectedVersion"))
            if (
                not isinstance(body["deckId"], str)
                or type(body["expectedVersion"]) is not int
            ):
                raise BridgeError("INVALID_REQUEST")
            revision = self.store.freeze(body["deckId"], body["expectedVersion"])
            return {"revisionId": revision["id"]}
        fields(body, ("revisionId", "ruleReleaseId", "requestId"))
        if any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in body.values()):
            raise BridgeError("INVALID_REQUEST")
        status = profile.client.status()
        if not status["identity"] or status["reauthenticationRequired"]:
            raise BridgeError("REAUTHENTICATION_REQUIRED", 401)
        identity = status["identity"]
        # Include server-assigned identity as well as origin + pinned trust root.
        owner = content_hash(
            {k: identity[k] for k in ("serverId", "userId", "deviceId")}
        )
        key = (profile.server.key, owner, body["requestId"])
        rule, revision = body["ruleReleaseId"], body["revisionId"]
        with self.store.db(True) as db:
            previous = db.execute(
                "SELECT * FROM online_deck_requests WHERE server=? AND identity=? AND request=?",
                key,
            ).fetchone()
            if previous:
                if (previous["rule"], previous["revision"]) != (rule, revision):
                    raise BridgeError("REQUEST_ID_CONFLICT", 409)
                entries = json.loads(previous["entries"])
                if previous["result"]:
                    return json.loads(previous["result"])
            else:
                row = db.execute(
                    """SELECT * FROM revisions WHERE id=?
                    AND id NOT IN (SELECT id FROM deleted_revisions)
                    AND deck_id IN (SELECT id FROM decks)""",
                    (revision,),
                ).fetchone()
                if row is None:
                    raise BridgeError("REVISION_NOT_FOUND", 404)
                snapshot = json.loads(row["body"])
                if content_hash(snapshot) != row["hash"]:
                    raise BridgeError("REVISION_CORRUPT", 409)
                entries = sorted(snapshot["entries"], key=lambda e: e["printingId"])
                digest = content_hash(entries)
                cached = db.execute(
                    """SELECT result FROM online_deck_requests
                    WHERE server=? AND identity=? AND rule=? AND content_hash=? AND result IS NOT NULL LIMIT 1""",
                    (key[0], owner, rule, digest),
                ).fetchone()
                result = cached["result"] if cached else None
                db.execute(
                    "INSERT INTO online_deck_requests VALUES(?,?,?,?,?,?,?,?)",
                    (*key, rule, digest, revision, json.dumps(entries), result),
                )
                if result:
                    return json.loads(result)
        # The durable request and immutable entries survive edits/deletion and unknown replies.
        # Only protocol fields cross the network, never local IDs, names or collection data.
        try:
            result = profile.client.submit_deck(
                entries, rule, request_id=body["requestId"]
            )
        except ClientError as error:
            if not error.retryable and error.status in (400, 422):
                # A definite payload rejection cannot have created a remote revision.
                with self.store.db(True) as db:
                    db.execute(
                        "DELETE FROM online_deck_requests WHERE server=? AND identity=? AND request=? AND result IS NULL",
                        key,
                    )
            raise
        with self.store.db(True) as db:
            db.execute(
                "UPDATE online_deck_requests SET result=? WHERE server=? AND identity=? AND request=?",
                (json.dumps(result), *key),
            )
        return result


class ProductResources:
    """Exact semantic version match; one deterministic printing per engine ID.

    The protocol currently identifies effects, not the original printing. Use the
    lexicographically first printing with local artwork, then printingId order.
    Snapshot image bytes in the existing bounded content-addressed native cache.
    """

    def __init__(self, media, fallback, sync_images=None):
        self.media, self.fallback, self.sync_images = media, fallback, sync_images
        self.version = MemoryCatalog.from_catalog(RAW).catalog_version
        self.lock = RLock()
        self.saved = {}
        for path in sorted(fallback.root.glob("product-catalog-*.json")):
            if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
                continue
            try:
                saved = json.loads(path.read_bytes())
                if (
                    content_hash(saved["cards"]) != saved["assetVersion"]
                    or path.name != "product-catalog-" + saved["assetVersion"] + ".json"
                ):
                    continue
                self.saved[saved["catalogVersion"], saved["assetVersion"]] = saved
            except (ValueError, KeyError, TypeError):
                continue

    def catalog(self, catalog_version, asset_version):
        with self.lock:
            if catalog_version != self.version or asset_version != "unavailable":
                if (catalog_version, asset_version) in self.saved:
                    return self.saved[catalog_version, asset_version]
                if asset_version == "unavailable":
                    archived = [
                        v for (c, _), v in self.saved.items() if c == catalog_version
                    ]
                    if archived:
                        return archived[-1]
                return self.fallback.catalog(catalog_version, asset_version)
            if (catalog_version, asset_version) in self.saved:
                return self.saved[catalog_version, asset_version]
            rows = {}
            candidates = self.media.decorate(
                [{**c, "image": select_image(c)} for c in RAW["cards"]]
            )
            for card in sorted(candidates, key=lambda c: c["printingId"]):
                engine = card.get("engineId")
                if not engine:
                    continue
                image = self.local_image(card.get("image", {}).get("url"))
                row = {
                    k: card.get(k, "")
                    for k in (
                        "engineId",
                        "englishName",
                        "cnName",
                        "effectStatus",
                        "reviewNote",
                    )
                }
                row["chineseDetails"] = card.get("chineseDetails", {})
                row.update(
                    printingId=card["printingId"],
                    image={"url": image, "label": "平台默认卡面（按效果映射）"},
                )
                if engine not in rows or (image and not rows[engine]["image"]["url"]):
                    rows[engine] = row
            cards = list(rows.values())
            version = content_hash(cards)
            result = {
                "catalogVersion": self.version,
                "assetVersion": version,
                "cards": cards,
                "missing": any(not c["image"]["url"] for c in cards),
                "printingPolicy": "first-local-printing-per-engine",
            }
            try:
                with self.fallback.lock:
                    self.fallback._write(
                        "product-catalog-" + version + ".json",
                        json.dumps(result, ensure_ascii=False).encode(),
                    )
            except BridgeError:
                result["cacheFull"] = True
            self.saved[self.version, "unavailable"] = result
            self.saved[self.version, version] = result
            return result

    def local_image(self, url):
        if not isinstance(url, str):
            return None
        match = re.fullmatch(r"/api/sync/images/([a-f0-9]{40})/([0-9]+)", url)
        if match and self.sync_images:
            # Reuse the existing commit/blob-verified, on-demand resource service.
            # This URL is local and immutable; images are not carried by API v2.
            return url
        locations = {
            "/card-images/": ROOT / ".catalog/images",
            "/uploaded-card-images/": self.media.directory,
        }
        for prefix, root in locations.items():
            if not url.startswith(prefix):
                continue
            path = root / url.removeprefix(prefix)
            if (
                not path.resolve().is_relative_to(root.resolve())
                or not path.is_file()
                or path.stat().st_size > MAX_IMAGE
            ):
                return None
            try:
                data = path.read_bytes()
                raster(data)
                key = sha256(data).hexdigest()
                with self.fallback.lock:
                    self.fallback._write(key, data)
                return "/resources/" + key
            except (OSError, BridgeError):
                return None
        # No browser remote image requests and no unpinned fallback download.
        return None

    def image(self, key):
        return self.fallback.image(key)
