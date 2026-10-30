"""Pinned full-card-face metadata, not artwork bytes or executable rules."""
from hashlib import sha256
from pathlib import Path
import re
from .common import ROOT, SyncError
from .http_source import HTTPSource, parse
from .repositories import canonical

INDEX = "resources/artwork/builtin-snapshot.json"


class BuiltinImages:
    def __init__(self, root=ROOT, *, catalog_path=None):
        self.commit, self.repository, self.rows = None, None, {}
        path = Path(root) / INDEX
        if not path.exists():
            return
        if path.is_symlink() or path.stat().st_size > 8 * 1024**2:
            raise SyncError("INVALID_IMAGE_INDEX", "随包卡面索引无效")
        value = parse(path.read_bytes())
        if not isinstance(value, dict) or set(value) != {"schema", "repository", "commit", "catalogFileSha256", "images"} or type(value["schema"]) is not int or value["schema"] != 1:
            raise SyncError("INVALID_IMAGE_INDEX", "随包卡面索引格式无效")
        if sha256(Path(catalog_path or Path(root)/"data/catalog/catalog.json").read_bytes()).hexdigest() != value["catalogFileSha256"]:
            raise SyncError("INVALID_IMAGE_INDEX", "随包卡面索引与卡表不匹配")
        self.repository = canonical(value["repository"])
        HTTPSource.validate_sha(value["commit"])
        rows = value["images"]
        if not isinstance(rows, dict) or not 1 <= len(rows) <= 100000:
            raise SyncError("INVALID_IMAGE_INDEX", "随包卡面索引大小无效")
        for cid, row in rows.items():
            if not re.fullmatch(r"[0-9]+", cid) or not isinstance(row, dict) or set(row) != {"path", "blob"}:
                raise SyncError("INVALID_IMAGE_INDEX", "随包卡面身份无效")
            HTTPSource.validate_path(row["path"])
            HTTPSource.validate_sha(row["blob"])
        self.commit, self.rows = value["commit"], rows
