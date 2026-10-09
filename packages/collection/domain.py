from __future__ import annotations
import csv, hashlib, io, json, math, random, re
from collections import Counter
from pathlib import Path
from packages.engine_adapter.cn_format import validate as policy_validate, RULES
from packages.rules.catalog import select_image
from packages.simulation.registry import support
from packages.cardpool.scope import check as scope_check

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "data/catalog/catalog.json"
if not CATALOG_PATH.exists():
    CATALOG_PATH = ROOT / "data/collection/catalog.json"
RAW = json.loads(CATALOG_PATH.read_text())
CARDS = {c["printingId"]: c for c in RAW["cards"]}
CATALOG_VERSION = RAW["version"]
AS_OF = RAW["asOf"]
CONDITIONS = ("未标注", "全新", "良好", "使用痕迹")


def content_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class DomainError(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.message, self.status = code, message, status


def require(condition, code, message, status=422):
    if not condition:
        raise DomainError(code, message, status)


def normalize(entries):
    require(
        isinstance(entries, list) and len(entries) <= 500,
        "BAD_ENTRIES",
        "卡牌列表格式错误或超过500行",
    )
    counts = Counter()
    for e in entries:
        require(
            isinstance(e, dict) and set(e) == {"printingId", "quantity"},
            "BAD_ENTRY",
            "仅接受卡牌ID和数量",
        )
        require(e["printingId"] in CARDS, "UNKNOWN_PRINTING", "无法识别的卡牌版本")
        q = e["quantity"]
        require(
            type(q) is int and 1 <= q <= 60, "BAD_QUANTITY", "每行数量必须为1至60的整数"
        )
        counts[e["printingId"]] += q
    require(
        all(q <= 60 for q in counts.values()), "BAD_QUANTITY", "同一版本合并后最多60张"
    )
    require(sum(counts.values()) <= 600, "TOO_MANY_CARDS", "草稿最多保存600张卡牌")
    return [{"printingId": p, "quantity": q} for p, q in sorted(counts.items())]


MESSAGES = {
    "BASIC_STATUS_UNKNOWN": ("基础宝可梦信息不完整", "补齐卡牌进化阶段资料后再校验"),
    "DECK_SIZE": ("卡组必须恰好60张", "调整卡牌数量至60张"),
    "NO_BASIC_POKEMON": ("需要至少1张基础宝可梦", "加入基础宝可梦"),
    "SAME_NAME_LIMIT": (
        "同名非基本能量卡合计不能超过4张",
        "减少同名卡，不同印刷合并计算",
    ),
    "ACE_SPEC_LIMIT": ("ACE SPEC合计不能超过1张", "只保留1张ACE SPEC"),
    "MARK_OR_REPRINT_NOT_ALLOWED": (
        "存在不符合本赛制标记的卡牌",
        "替换为当前赛制允许的印刷",
    ),
    "NOT_RELEASED": ("卡牌在选择的日期尚未发售", "核对对战日期"),
    "EFFECT_NOT_VERIFIED": ("存在尚未审核的效果", "更换为已审核卡牌"),
    "PRINTING_UNVERIFIED": ("印刷资料尚未确认", "先完成资料审核"),
    "SNAPSHOT_NOT_EFFECTIVE": ("所选规则快照尚未生效", "使用快照生效后的日期"),
}


def validation(entries):
    entries = normalize(entries)
    base = policy_validate(
        [
            {"quantity": e["quantity"], "printing": CARDS[e["printingId"]]}
            for e in entries
        ],
        AS_OF,
    )
    issues = []
    names = Counter()
    for e in entries:
        c = CARDS[e["printingId"]]
        if not c.get("basicEnergyType"):
            names[c["nameLimitKey"]] += e["quantity"]
    for i in base["issues"]:
        code = i["code"]
        text, fix = MESSAGES.get(code, ("卡牌资料待核对", "查看卡牌详情与来源"))
        refs = [
            e["printingId"]
            for e in entries
            if (
                code == "SAME_NAME_LIMIT"
                and names[CARDS[e["printingId"]]["nameLimitKey"]] > 4
            )
            or (code == "ACE_SPEC_LIMIT" and CARDS[e["printingId"]]["aceSpec"])
        ]
        issues.append(
            {
                "code": code,
                "severity": "error",
                "cardRefs": refs,
                "explanation": text,
                "suggestedFix": fix,
            }
        )
    execution = support(entries, CARDS)
    battle_scope = scope_check(entries, CARDS)
    if battle_scope["outside"]:
        issues.append({
            "code": "OUTSIDE_BATTLE_SCOPE", "severity": "error",
            "cardRefs": battle_scope["outside"],
            "explanation": "P4 对战范围为 G/H/I/J 标记及八种基本能量",
            "suggestedFix": "替换为范围内且已实现效果的卡牌；旧标记重印仅保留资料",
        })
    effect_counts = lambda es: sum(
        (Counter({CARDS[e["printingId"]]["engineId"]: e["quantity"]}) for e in es),
        Counter(),
    )
    preconstructed = any(
        effect_counts(entries) == effect_counts(t["entries"]) for t in RAW["templates"]
    )
    if execution["missing"]:
        issues.append(
            {
                "code": "EFFECT_UNSUPPORTED",
                "severity": "error",
                "cardRefs": execution["missing"],
                "explanation": "部分卡牌尚未发布可执行效果",
                "suggestedFix": "查看标出的卡牌；使用已发布支持卡池",
            }
        )
    elif not preconstructed:
        issues.append(
            {
                "code": "EXPERIMENTAL_COMPOSITION",
                "severity": "warning",
                "cardRefs": [],
                "explanation": "支持卡池内的自定义组合，可进行实验性对战；尚未获得竞技强度认证",
                "suggestedFix": "通过回放反馈规则或决策问题",
            }
        )
    return {
        "total": sum(e["quantity"] for e in entries),
        "legality": base["legality"],
        "structure": "unknown"
        if any(i["code"] == "BASIC_STATUS_UNKNOWN" for i in issues)
        else "valid"
        if not any(
            i["code"]
            in ("DECK_SIZE", "NO_BASIC_POKEMON", "SAME_NAME_LIMIT", "ACE_SPEC_LIMIT")
            for i in issues
        )
        else "invalid",
        "engine": "verified"
        if base["playable"] and execution["supported"] and battle_scope["supported"]
        else "unverified",
        "playable": base["playable"] and execution["supported"] and battle_scope["supported"],
        "battleScope": battle_scope,
        "executionSupport": execution["status"],
        "effectReleaseVersion": execution["version"],
        "interactionEvidence": "preconstructed-regression"
        if preconstructed
        else "sampled-combinations",
        "aiEvaluation": "unrated",
        "issues": issues,
        "formatId": RAW["formatId"],
        "asOf": AS_OF,
    }


def analysis(entries, seed=0):
    entries = normalize(entries)
    counts = Counter()
    basics = 0
    unknown_basics = 0
    deck = []
    chains = []
    costs = []
    for e in entries:
        c = CARDS[e["printingId"]]
        q = e["quantity"]
        counts[c["category"]] += q
        basics += q * bool(c["isBasicPokemon"])
        unknown_basics += (
            q if c["category"] == "宝可梦" and c["isBasicPokemon"] is None else 0
        )
        deck.extend([c["printingId"]] * q)
        if c["category"] == "宝可梦":
            chains.append(
                {
                    "name": c["cnName"],
                    "from": c["evolvesFrom"],
                    "stage": c["stage"],
                    "quantity": q,
                }
            )
            costs.append({"name": c["cnName"], "attacks": c["attacks"]})
    n = len(deck)
    prob = (
        1 - math.comb(n - basics, 7) / math.comb(n, 7)
        if n >= 7 and n - basics >= 7
        else (1.0 if n >= 7 else None)
    )
    if unknown_basics:
        prob = None
    rng = random.Random(seed)
    rng.shuffle(deck)
    return {
        "categories": dict(counts),
        "basicPokemon": basics,
        "unknownBasicPokemon": unknown_basics,
        "openingBasicProbability": prob,
        "seed": seed,
        "openingHand": deck[:7],
        "openingHasBasic": True
        if any(CARDS[p]["isBasicPokemon"] for p in deck[:7])
        else None
        if any(CARDS[p]["isBasicPokemon"] is None for p in deck[:7])
        else False,
        "openingNote": "初始7张样本，尚未重抽；不含检索、调度或展开策略。",
        "evolutions": chains,
        "attackCosts": costs,
    }


def missing(entries, collection, mode="exact"):
    require(
        mode in ("exact", "equivalent"), "BAD_MODE", "缺卡模式必须为指定印刷或规则等价"
    )
    key = lambda p: p if mode == "exact" else CARDS[p]["definitionId"]
    available = Counter()
    required = Counter()
    representative = {}
    for r in collection:
        available[key(r["printingId"])] += r["quantity"]
    for e in normalize(entries):
        required[key(e["printingId"])] += e["quantity"]
        representative[key(e["printingId"])] = e["printingId"]
    rows = [
        {
            "printingId": representative[k],
            "required": q,
            "owned": available[k],
            "missing": max(0, q - available[k]),
        }
        for k, q in required.items()
    ]
    return {
        "mode": mode,
        "rows": rows,
        "totalMissing": sum(r["missing"] for r in rows),
        "ownedUsed": sum(min(r["required"], r["owned"]) for r in rows),
    }


def catalog_view(
    query="",
    category="",
    product="",
    owned="",
    collection=(),
    effect="",
    element="",
    pack="",
):
    amounts = Counter()
    for e in collection:
        amounts[e["printingId"]] += e["quantity"]
    q = re.sub(r"\s+", "", query).casefold()
    out = []
    for c in CARDS.values():
        hay = " ".join(
            [
                c["cnName"],
                *c["aliases"],
                c["productCode"] or "",
                c["collectorNumber"] or "",
                c["printingId"],
            ]
        )
        if q and q not in re.sub(r"\s+", "", hay).casefold():
            continue
        if category and category != c["category"]:
            continue
        if product and product != c["productCode"]:
            continue
        if pack and pack not in c.get("productIds", []):
            continue
        if effect and effect != c["effectStatus"]:
            continue
        if element and element != c["pokemonType"]:
            continue
        n = amounts[c["printingId"]]
        if owned == "owned" and not n or owned == "missing" and n:
            continue
        selected_image = select_image(c)
        if (selected_image.get("url") or "").startswith("/card-images/"):
            local = ROOT / ".catalog/images" / Path(selected_image["url"]).name
            if not local.is_file():
                selected_image["url"] = selected_image.get("remoteUrl")
        out.append({**c, "owned": n, "image": selected_image})
    return out


def parse_import(text, kind="deck", resolutions=None):
    require(kind in ("deck", "collection"), "BAD_IMPORT_KIND", "不支持的导入类型")
    require(
        isinstance(text, str) and len(text) <= 200000,
        "IMPORT_TOO_LARGE",
        "导入内容最多200KB",
    )
    resolutions = resolutions or {}
    text = text.lstrip("\ufeff").strip()
    rows = []
    warnings = []
    require(bool(text), "EMPTY_IMPORT", "导入内容为空")
    lines = text.splitlines()
    is_csv = "," in lines[0]
    try:
        if is_csv:
            reader = csv.DictReader(io.StringIO(text), strict=True)
            headers = reader.fieldnames or []
            require(
                "quantity" in headers
                and any(k in headers for k in ("printingId", "name")),
                "BAD_HEADERS",
                "CSV需包含quantity，以及printingId或name列",
            )
            require(len(headers) == len(set(headers)), "BAD_HEADERS", "CSV列名不能重复")
            source = list(reader)
            require(
                all(None not in row for row in source), "BAD_CSV", "CSV数据列数超过表头"
            )
        else:
            source = []
            for line in lines:
                if not line.strip() or line.strip().startswith("#"):
                    continue
                m = re.fullmatch(r"\s*(\d+)\s+(.+?)\s*", line)
                source.append(
                    {"quantity": m[1], "name": m[2]}
                    if m
                    else {"quantity": "", "name": line}
                )
    except csv.Error as e:
        raise DomainError("BAD_CSV", str(e))
    require(len(source) <= 500, "TOO_MANY_ROWS", "最多导入500行")
    for idx, row in enumerate(source):
        name = (row.get("name") or "").strip()
        pid = (row.get("printingId") or "").strip()
        cond = (row.get("condition") or "未标注").strip()
        notes = row.get("notes") or ""
        if notes.startswith("'") and notes[1:2] and notes[1:2] in "'=+-@\t\r":
            notes = notes[1:]
        qtext = (row.get("quantity") or "").strip()
        q = int(qtext) if len(qtext) <= 5 and re.fullmatch(r"\d+", qtext) else -1
        wishtext = (row.get("wishlist") or "").strip().casefold()
        wishlist = wishtext in ("true", "1")
        candidates = []
        if not pid and name in CARDS:
            pid = name
        if pid in CARDS:
            candidates = [pid]
        elif not pid:
            m = re.fullmatch(
                r"(.+?)\s+([A-Za-z][A-Za-z0-9.]*C)\s+([A-Za-z0-9-]+)(?:/[A-Za-z0-9]+)?",
                name,
            )
            product = (row.get("productCode") or (m[2] if m else "")).casefold()
            number = (row.get("collectorNumber") or (m[3] if m else "")).split("/")[0]
            if number.isdecimal():
                number = number.zfill(3)
            needle = (m[1] if m else name).replace(" ", "").casefold()
            for c in CARDS.values():
                aliases = [c["cnName"], c["englishName"], *c["aliases"]]
                if (
                    needle in [a.replace(" ", "").casefold() for a in aliases]
                    and (not product or (c["productCode"] or "").casefold() == product)
                    and (number in ("", "000") or c["collectorNumber"] == number)
                ):
                    candidates.append(c["printingId"])
        selected = resolutions.get(
            str(idx), candidates[0] if len(candidates) == 1 else None
        )
        require(
            selected is None or selected in candidates,
            "BAD_RESOLUTION",
            "确认的版本不在本行候选中",
        )
        error = (
            "数量超限：卡组1至60，收藏0至9999"
            if not (1 <= q <= 60 if kind == "deck" else 0 <= q <= 9999)
            else None
        )
        if wishtext not in ("", "true", "false", "1", "0"):
            error = "wishlist必须为true或false"
        if cond not in CONDITIONS:
            error = "未知品相"
        if len(notes) > 500:
            error = "备注最多500字"
        if not candidates:
            error = "未找到匹配卡牌，请补充准确ID或名称、系列与编号"
        elif not selected:
            error = "同名存在多个印刷版本，请选择"
        rows.append(
            {
                "row": idx,
                "name": name or pid,
                "quantity": q,
                "printingId": selected,
                "candidates": candidates,
                "condition": cond,
                "notes": notes,
                "wishlist": wishlist,
                "error": error,
            }
        )
    groups = {}
    note_groups = {}
    for r in rows:
        if r["error"]:
            continue
        k = (
            (r["printingId"], r["condition"])
            if kind == "collection"
            else (r["printingId"],)
        )
        if kind == "collection":
            note_groups.setdefault(k, set()).add(r["notes"])
        if k in groups:
            warnings.append(f"第{r['row'] + 1}行与相同版本合并")
            groups[k]["quantity"] += r["quantity"]
            if kind == "collection":
                groups[k]["wishlist"] = groups[k]["wishlist"] or r["wishlist"]
        else:
            groups[k] = {
                k: r[k]
                for k in (
                    ("printingId", "quantity", "condition", "notes", "wishlist")
                    if kind == "collection"
                    else ("printingId", "quantity")
                )
            }
    if kind == "collection":
        for k, group in groups.items():
            group["notes"] = "；".join(sorted(n for n in note_groups[k] if n))
    merged = list(groups.values())
    ready = bool(rows) and all(not r["error"] for r in rows)
    if ready and (
        (
            sum(e["quantity"] for e in merged) > 600
            or any(e["quantity"] > 60 for e in merged)
        )
        if kind == "deck"
        else any(e["quantity"] > 9999 or len(e["notes"]) > 500 for e in merged)
    ):
        ready = False
        warnings.append("合并后的数量或备注超限，请调整导入内容")
    return {
        "rows": rows,
        "entries": merged,
        "ready": ready,
        "warnings": warnings,
        "kind": kind,
    }


def export_csv(rows, kind):
    fields = ["printingId", "quantity"] + (
        ["condition", "notes", "wishlist"] if kind == "collection" else []
    )
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in rows:
        item = {k: r.get(k, "") for k in fields}
        for k, v in item.items():
            if isinstance(v, str) and v.startswith(
                ("'", "=", "+", "-", "@", "\t", "\r")
            ):
                item[k] = "'" + v
        w.writerow(item)
    return "\ufeff" + buf.getvalue()
