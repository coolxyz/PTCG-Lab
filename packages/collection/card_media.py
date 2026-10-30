"""Card presentation backed only by verified online/cache or local-clone images."""

import json
from functools import lru_cache
from packages.collection.domain import ROOT

@lru_cache(maxsize=2)
def _details(stamp):
    path = ROOT / "data/catalog/card-details.json"
    return (
        json.loads(path.read_text(encoding="utf-8")).get("cards", {})
        if path.exists()
        else {}
    )


def details():
    path = ROOT / "data/catalog/card-details.json"
    return _details(path.stat().st_mtime_ns if path.exists() else 0)


class CardMedia:
    def __init__(self, store):
        self.store = store
        self.artwork = None

    def decorate(self, cards):
        source = details()
        result = []
        for card in cards:
            info = source.get(card["printingId"], {})
            c = {**card, "chineseDetails": {
                k: info[k] for k in ("sections", "source", "revision", "textStatus", "imageGap") if k in info
            }}
            result.append(self._artwork(c))
        return result

    def _artwork(self, card):
        if self.artwork is None:
            # Internal current-release workers return metadata to the host;
            # only the host resolves it through the selected verified source.
            return card
        url = self.artwork.url(card) if self.artwork else None
        return {**card, "image": {"url": url, "label": "卡图 · 本机资源" if url else "暂无经过索引校验的卡图"}}

    def display(self, cards):
        return [self._artwork(card) for card in cards]
