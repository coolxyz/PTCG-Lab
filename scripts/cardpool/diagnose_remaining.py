"""Read-only reasons the current compiler rejects unadmitted target pages."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cardpool.catalog_inventory import load_articles, read
from scripts.cardpool.compile_plain import compile_rule
from scripts.cardpool.trainer_rules import compile_trainer
from scripts.cardpool.special_energy_rules import compile_energy
from packages.cardpool.scope import includes


def main():
    catalog = read(ROOT / "data/catalog/catalog.json")
    pages = load_articles()
    rows = []
    groups = {}
    for c in catalog["cards"]:
        if includes(c) and c.get("effectStatus") != "verified" and c.get("catalogStatus") == "listed" and c.get("releasedAt") and c["releasedAt"] <= catalog["asOf"]:
            groups.setdefault(c.get("cardPage"), []).append(c["printingId"])
    for title, printings in groups.items():
        page = pages.get(title, {})
        info = {"page": title, "printings": printings}
        def trace(frame, event, arg):
            if frame.f_code is compile_rule.__code__ and event == "return" and arg is None:
                info.update(line=frame.f_lineno)
                info.update({k: frame.f_locals.get(k) for k in ("h", "end", "p", "english", "previous")})
            return trace
        sys.settrace(trace)
        try:
            spec = compile_rule(page)
        finally:
            sys.settrace(None)
        spec = spec or compile_trainer(page) or compile_energy(page)
        if spec:
            info["compiled"] = spec["name"]
        rows.append(info)
    out = ROOT / "artifacts/cardpool/remaining-diagnostics.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=lambda x: sorted(x) if isinstance(x, set) else str(x)) + "\n", encoding="utf8")
    print({"pages": len(rows), "printings": sum(len(r["printings"]) for r in rows), "compiledUnpublished": sum(len(r["printings"]) for r in rows if r.get("compiled"))})


if __name__ == "__main__":
    main()
