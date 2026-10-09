"""Polite, cached public-source retrieval. No credentials or browser state."""

from pathlib import Path
import hashlib, json, time, urllib.request, urllib.parse, http.client, uuid

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".catalog/sources"
CACHE.mkdir(parents=True, exist_ok=True)
AGENT = "PTCG-local-catalog/0.1 (personal catalogue; cached low-rate retrieval)"


def retrieve(url, kind="html"):
    path = CACHE / (hashlib.sha256(url.encode()).hexdigest() + "." + kind)
    if path.exists():
        return path.read_bytes(), path
    time.sleep(0.6)
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, http.client.RemoteDisconnected):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    staged = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    staged.write_bytes(data)
    staged.replace(path)
    return data, path


def wiki(title):
    url = "https://wiki.52poke.com/api.php?" + urllib.parse.urlencode(
        {"action": "parse", "page": title, "prop": "text|revid", "format": "json"}
    )
    data, path = retrieve(url, "json")
    result = json.loads(data)
    if "error" in result:
        raise ValueError(result["error"])
    parsed = result["parse"]
    return parsed, {
        "url": "https://wiki.52poke.com/wiki/" + urllib.parse.quote(title),
        "apiUrl": url,
        "revision": parsed.get("revid"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "cachePath": str(path.relative_to(ROOT)),
    }
