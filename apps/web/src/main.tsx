import { DetailImage } from "./DetailImage";
import React, { useState, useEffect, useLayoutEffect, useRef, useMemo } from "react";
import { createRoot } from "react-dom/client";
import {
  Search,
  Library,
  Layers,
  Heart,
  Plus,
  Minus,
  ArrowUpRight,
  ArrowDownToLine,
  Upload,
  Check,
  ChevronRight,
  X,
  SlidersHorizontal,
  History,
  Shuffle,
  CheckCircle2,
  AlertCircle,
  Copy,
  Leaf,
  Package,
  BookOpen,
  Swords,
  Trash2,
} from "lucide-react";
import "./style.css";
import { Battle } from "./Battle";
import { Sync } from "./Sync";
import { CardArtwork, CardDescription, type ChineseDetails } from "./CardArtwork";
import { CardVariants } from './CardVariants';

type Entry = { printingId: string; quantity: number };
type Card = Entry & {
  cnName: string;
  effectStatus: string;
  sourceVerified: boolean;
  pendingSourceException?: boolean;
  productName?: string;
  productIds?: string[];
  englishName: string;
  productCode: string | null;
  collectorNumber: string | null;
  identityKind: string;
  catalogStatus?: string;
  printedNumber?: string;
  category: string;
  subtype: string;
  hp: number | null;
  mark: string | null;
  pokemonType: string;
  reviewNote: string;
  sourceEvidence: string[];
  engineId: string;
  stage: string;
  image: { url: string | null; label: string; userUploaded?: boolean; source?: string };
  chineseDetails?: ChineseDetails;
  owned: number;
  variants?: { variantId: string }[];
};
type Holding = Entry & { condition: string; notes: string; wishlist: boolean };
type Deck = {
  id: string;
  name: string;
  entries: Entry[];
  version: number;
  updatedAt: string;
};
async function api(
  path: string,
  body?: unknown,
  method?: string,
): Promise<any> {
  const r = await fetch("/api" + path, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      data.message || data.detail?.[0]?.msg || "操作未完成，请稍后重试",
    );
  return data;
}
const count = (es: Entry[]) => es.reduce((s, e) => s + e.quantity, 0);
const sign = (d: Deck) => JSON.stringify([d.name, d.entries]);
const typeLabels: Record<string, string> = {
  GRASS: "草",
  LIGHTNING: "雷",
  FIGHTING: "斗",
  FAIRY: "妖",
  METAL: "钢",
  PSYCHIC: "超",
  DARK: "恶",
  DRAGON: "龙",
  COLORLESS: "无",
  FIRE: "火",
  WATER: "水",
  NONE: "—",
};
const stageLabels: Record<string, string> = {
  V_EVOLUTION: "V进化",
  MEGA_EVOLUTION: "M进化",
  BREAK: "BREAK",
  LEVEL_UP: "升级",
  BASIC: "基础",
  STAGE_1: "1阶进化",
  STAGE_2: "2阶进化",
};
const subtypeLabels: Record<string, string> = {
  ITEM: "物品",
  TOOL: "宝可梦道具",
  SUPPORTER: "支援者",
  STADIUM: "竞技场",
};
function App() {
  const [cardPage, setCardPage] = useState(0);
  const [effect, setEffect] = useState("");
  const [failedImages, setFailedImages] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(
      window.location.hash === "#sync" ? "sync" : window.location.hash === "#battle" ? "battle" : "cards",
    ),
    [cards, setCards] = useState<Card[]>([]),
    [meta, setMeta] = useState<any>(null),
    [collection, setCollection] = useState<{
      version: number;
      entries: Holding[];
    }>({ version: 0, entries: [] }),
    [decks, setDecks] = useState<Deck[]>([]);
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState(""),
    [product, setProduct] = useState(""),
    [pack, setPack] = useState(""),
    [owned, setOwned] = useState(""),
    [imageFilter, setImageFilter] = useState(""),
    [element, setElement] = useState("");
  const [detail, setDetail] = useState<Card | null>(null),
    [importKind, setImportKind] = useState<string | null>(null),
    [toast, setToast] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Deck | null>(null),
    [saveStatus, setSaveStatus] = useState("已保存"),
    [check, setCheck] = useState<any>(null),
    [stats, setStats] = useState<any>(null),
    [missing, setMissing] = useState<any>(null),
    [missingMode, setMissingMode] = useState("exact"),
    [seed, setSeed] = useState(42),
    [revisions, setRevisions] = useState<any[] | null>(null);
  const [batches, setBatches] = useState<any[] | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [wishOnly, setWishOnly] = useState(false);
  const queue = useRef(Promise.resolve()),
    saved = useRef(new Map<string, string>()),
    versions = useRef(new Map<string, number>()),
    current = useRef(draft);
  current.current = draft;
  const cardMap = useMemo(
    () => Object.fromEntries(cards.map((c) => [c.printingId, c])),
    [cards],
  );
  useEffect(
    () => setCardPage(0),
    [query, category, product, pack, owned, element, effect, page, wishOnly],
  );
  const qty = (id: string) =>
    collection.entries
      .filter((e) => e.printingId === id)
      .reduce((s, e) => s + e.quantity, 0);
  const holding = (id: string, condition = "未标注"): Holding =>
    collection.entries.find(
      (e) => e.printingId === id && e.condition === condition,
    ) || { printingId: id, condition, quantity: 0, notes: "", wishlist: false };
  const wish = (id: string) =>
    collection.entries.some((e) => e.printingId === id && e.wishlist);
  async function refreshCollection() {
    const next = await api("/collection");
    setCollection(previous => next.version >= previous.version ? next : previous);
  }
  async function refreshDecks() {
    setDecks(await api("/decks"));
  }
  async function act(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    act(async () => {
      await Promise.all([
        api("/cards").then(c => setCards(c.cards)),
        api("/meta").then(setMeta),
        api("/collection").then(setCollection),
        api("/decks").then(setDecks),
      ]);
    });
  }, []);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 4000);
      return () => clearTimeout(t);
    }
  }, [toast]);
  const pendingSaves = useRef(0);
  function persist(d: Deck): Promise<void> {
    pendingSaves.current++;
    const task = queue.current
      .catch(() => {})
      .then(async () => {
        if (saved.current.get(d.id) === sign(d)) {
          if (current.current?.id === d.id && sign(current.current) === sign(d))
            setSaveStatus("已保存");
          return;
        }
        setSaveStatus("保存中…");
        try {
          const result = await api(
            "/decks/" + d.id,
            {
              name: d.name,
              entries: d.entries,
              expectedVersion: versions.current.get(d.id) || d.version,
            },
            "PUT",
          );
          versions.current.set(d.id, result.version);
          saved.current.set(d.id, sign(d));
          setDecks((ds) => [result, ...ds.filter((x) => x.id !== d.id)]);
          if (current.current?.id === d.id) {
            setDraft((prev) =>
              prev?.id === d.id ? { ...prev, version: result.version } : prev,
            );
            setSaveStatus(
              sign(current.current) === sign(d) ? "已保存" : "等待保存",
            );
          }
        } catch (e) {
          setSaveStatus("保存失败");
          throw e;
        }
      });
    queue.current = task;
    return task.finally(() => {
      pendingSaves.current--;
    });
  }
  useEffect(() => {
    if (!draft) return;
    setSaveStatus("等待保存");
    const d = draft;
    const t = setTimeout(
      () => persist(d).catch((e) => setError(e.message)),
      650,
    );
    return () => clearTimeout(t);
  }, [draft?.id, draft?.name, JSON.stringify(draft?.entries)]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      const d = current.current;
      if (
        d &&
        (pendingSaves.current > 0 || saved.current.get(d.id) !== sign(d))
      ) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  useEffect(() => {
    if (!draft) {
      setCheck(null);
      return;
    }
    let live = true;
    const entries = draft.entries;
    Promise.all([
      api("/deck-validations", { entries }),
      api("/deck-analysis", { entries, seed }),
      api("/missing-cards", { entries, mode: missingMode }),
    ])
      .then(([v, a, m]) => {
        if (live) {
          setCheck(v);
          setStats(a);
          setMissing(m);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [JSON.stringify(draft?.entries), seed, collection.version, missingMode]);
  async function openDeck(d: Deck) {
    if (current.current) await persist(current.current);
    saved.current.set(d.id, sign(d));
    versions.current.set(d.id, d.version);
    setDraft(d);
    setPage("decks");
    setSaveStatus("已保存");
    setQuery("");
    setCategory("");
    setOwned("");
    setProduct("");
    setElement("");
  }
  async function createDeck(name: string, entries: Entry[] = []) {
    if (current.current) await persist(current.current);
    const d = await api("/decks", { name: name.slice(0, 80), entries });
    await refreshDecks();
    await openDeck(d);
    setToast("卡组草稿已创建");
  }
  async function deleteDeck(d: Deck) {
    if (!window.confirm(`删除卡组“${d.name}”？此操作无法撤销。已有对局和回放所需的版本记录会保留。`)) return;
    try {
      await api("/decks/" + d.id, { expectedVersion: d.version }, "DELETE");
      saved.current.delete(d.id);
      versions.current.delete(d.id);
      setToast("卡组已删除");
    } finally {
      await refreshDecks();
    }
  }
  function changeDeck(id: string, delta: number) {
    if (!draft) return;
    const es = [...draft.entries];
    const i = es.findIndex((e) => e.printingId === id),
      n = (i < 0 ? 0 : es[i].quantity) + delta;
    if (n > 60 || n < 0 || (delta > 0 && count(es) >= 600)) return;
    if (i < 0) es.push({ printingId: id, quantity: n });
    else if (n === 0) es.splice(i, 1);
    else es[i] = { ...es[i], quantity: n };
    setDraft({ ...draft, entries: es });
  }
  async function saveHold(r: Holding) {
    const next = await api(
      "/collection",
      { expectedVersion: collection.version, changes: [r] },
      "PUT",
    );
    setCollection(previous => next.version >= previous.version ? next : previous);
  }
  async function modifyHolding(id: string, delta: number) {
    const h = holding(id);
    if (h.quantity + delta < 0) return;
    await saveHold({ ...h, quantity: h.quantity + delta });
  }
  async function toggleWish(id: string) {
    const existing = collection.entries.filter(
      (e) => e.printingId === id && e.wishlist,
    );
    const changes = existing.length
      ? existing.map((e) => ({ ...e, wishlist: false }))
      : [{ ...holding(id), wishlist: true }];
    await api(
      "/collection",
      { expectedVersion: collection.version, changes },
      "PUT",
    );
    await refreshCollection();
  }
  async function recoverDraft() {
    if (!draft) return;
    const d = await api("/decks", {
      name: (draft.name.slice(0, 70) || "未命名") + " · 冲突副本",
      entries: draft.entries,
    });
    saved.current.set(d.id, sign(d));
    versions.current.set(d.id, d.version);
    setDraft(d);
    setSaveStatus("已保存");
    await refreshDecks();
    setToast("已将本页内容另存为副本，原卡组保持服务器版本");
  }
  const visible = cards.filter((c) => {
    const q = query.toLowerCase().replace(/\s+/g, "");
    const hay = [
      c.cnName,
      c.englishName,
      c.productCode,
      c.collectorNumber,
      c.printingId,
      c.engineId,
    ]
      .join("")
      .toLowerCase()
      .replace(/\s+/g, "");
    return (
      (!q || hay.includes(q)) &&
      (!category || c.category === category) &&
      (!imageFilter || (imageFilter === "missing" ? !c.image.url : imageFilter === "failed" ? failedImages.has(c.printingId) : c.image.userUploaded)) &&
      (!product || c.productCode === product) &&
      (!pack || c.productIds?.includes(pack)) &&
      (!effect || c.effectStatus === effect) &&
      (!element || c.pokemonType === element) &&
      (!owned ||
        (owned === "owned" ? qty(c.printingId) > 0 : qty(c.printingId) === 0))
    );
  });
  if (pack || product) {
    const numberOrder = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
    visible.sort((a, b) => {
      const left = a.collectorNumber?.trim() || "";
      const right = b.collectorNumber?.trim() || "";
      return Number(!left) - Number(!right)
        || numberOrder.compare(left, right)
        || numberOrder.compare(a.printingId, b.printingId);
    });
  }
  const collectionCards = visible.filter(
    (c) =>
      (qty(c.printingId) > 0 || wish(c.printingId)) &&
      (!wishOnly || wish(c.printingId)),
  );
  const pageSize = 72;
  const results = page === "collection" ? collectionCards : visible;
  const pageCount = Math.max(1, Math.ceil(results.length / pageSize));
  const activePage = Math.min(cardPage, pageCount - 1);
  const pageCards = results.slice(
    activePage * pageSize,
    (activePage + 1) * pageSize,
  );
  const pagination = (
    <div className="section-line" aria-label="卡牌分页">
      <button
        className="secondary small"
        disabled={activePage === 0}
        onClick={() => setCardPage(activePage - 1)}
      >
        上一页
      </button>
      <span>
        第 {activePage + 1} / {pageCount} 页 · 每页 {pageSize} 张
      </span>
      <button
        className="secondary small"
        disabled={activePage + 1 >= pageCount}
        onClick={() => setCardPage(activePage + 1)}
      >
        下一页
      </button>
    </div>
  );
  const products = useMemo(() => {
    const names = new Map<string, string>();
    const shared = new Set(
      cards
        .filter((c) => (c.productIds?.length || 0) > 1)
        .map((c) => c.productCode),
    );
    for (const c of cards) {
      if (c.productCode && !names.has(c.productCode))
        names.set(
          c.productCode,
          shared.has(c.productCode)
            ? "多卡包共用编号"
            : c.productName || c.productCode,
        );
    }
    return [...names.entries()];
  }, [cards]);
  const packs = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of cards)
      for (const id of c.productIds || [])
        counts.set(id, (counts.get(id) || 0) + 1);
    return (meta?.products || [])
      .map((p: { id: string; name: string; releasedAt: string | null }) => ({
        ...p,
        count: counts.get(p.id) || 0,
      }))
      .sort(
        (
          a: { releasedAt: string | null; name: string },
          b: { releasedAt: string | null; name: string },
        ) =>
          (b.releasedAt || "").localeCompare(a.releasedAt || "") ||
          a.name.localeCompare(b.name, "zh-CN"),
      );
  }, [meta, cards]);
  const quantityTotal = collection.entries.reduce((s, e) => s + e.quantity, 0),
    wishCount = new Set(
      collection.entries.filter((e) => e.wishlist).map((e) => e.printingId),
    ).size;
  const badge = (c: Card) => (
    <span className={"type type-" + c.pokemonType}>
      {c.category === "宝可梦"
        ? typeLabels[c.pokemonType] || c.pokemonType
        : c.category === "能量"
          ? "能"
          : "训"}
    </span>
  );
  const filters = (
    <>
      <div className="search-row">
        <div className="search">
          <Search size={18} />
          <input
            aria-label="搜索卡牌"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索卡名、英文别名或卡牌编号…"
          />
          <kbd>查卡</kbd>
        </div>
        <select
          aria-label="持有情况"
          value={owned}
          onChange={(e) => setOwned(e.target.value)}
        >
          <option value="">全部持有情况</option>
          <option value="owned">已持有</option>
          <option value="missing">未持有</option>
        </select>
        <select aria-label="卡图状态" value={imageFilter} onChange={e => { setImageFilter(e.target.value); setCardPage(0); }}>
          <option value="">全部卡图状态</option>
          <option value="missing">待补卡图</option>
          <option value="failed">本次加载失败</option>
          <option value="uploaded">用户上传</option>
        </select>
        <a className="text-link" href="/api/card-image-gaps">导出缺失卡图清单</a>
      </div>
      <div className="pack-filter">
        <label htmlFor="pack-filter">
          <Package size={16} /> 扩充包
        </label>
        <select
          id="pack-filter"
          aria-label="扩充包"
          value={pack}
          onChange={(e) => {
            setPack(e.target.value);
            setProduct("");
          }}
        >
          <option value="">全部扩充包与预组</option>
          {packs.map(
            (p: {
              id: string;
              name: string;
              upstreamCode?: string;
              releasedAt: string | null;
              count: number;
            }) => (
              <option key={p.id} value={p.id}>
                {p.name}{p.upstreamCode ? ` · ${p.upstreamCode}` : !p.id.startsWith("chs-product:") ? ` · ${p.id.split(":")[0]}` : ""} · {p.count} 张
              </option>
            ),
          )}
        </select>
        {pack && (
          <button className="text-link" onClick={() => setPack("")}>
            清除扩充包
          </button>
        )}
      </div>
      <div className="filter-row">
        <div className="segmented">
          {["", "宝可梦", "训练家", "能量"].map((t) => (
            <button
              key={t}
              className={category === t ? "active" : ""}
              onClick={() => setCategory(t)}
            >
              {t || "全部卡牌"}
            </button>
          ))}
        </div>
        <div className="selects">
          <SlidersHorizontal size={15} />
          <select
            aria-label="效果状态"
            value={effect}
            onChange={(e) => setEffect(e.target.value)}
          >
            <option value="">全部验证状态</option>
            <option value="verified">效果已验证</option>
            <option value="unverified">效果待验证</option>
          </select>
          <select
            aria-label="系列"
            value={product}
            onChange={(e) => {
              setProduct(e.target.value);
              setPack("");
            }}
          >
            <option value="">全部系列</option>
            {products.map(([code, name]) => (
              <option key={code} value={code!}>
                {code} · {name}
              </option>
            ))}
          </select>
          <select
            aria-label="属性"
            value={element}
            onChange={(e) => setElement(e.target.value)}
          >
            <option value="">全部属性</option>
            {Object.entries(typeLabels)
              .filter(([k]) => k !== "NONE")
              .map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
          </select>
        </div>
      </div>
    </>
  );
  function renderCardTile(c: Card, building = false) {
    return (
      <article className="card-tile" key={c.printingId}>
        <button
          className={
            "card-face " +
            (c.category === "宝可梦"
              ? "pokemon"
              : c.category === "能量"
                ? "energy"
                : "trainer")
          }
          onClick={() => setDetail(c)}
          aria-label={"查看 " + c.cnName + " " + (c.collectorNumber || "储备")}
        >
          {c.image.url && !failedImages.has(c.printingId) && (
            <img
              className="tile-original"
              src={c.image.url}
              alt={c.cnName + " · " + c.image.label}
              loading="lazy"
              onError={(e) => {
                e.currentTarget.style.display = "none";
                setFailedImages((prev) => new Set(prev).add(c.printingId));
              }}
            />
          )}
          <div className="face-top">
            {badge(c)}
            <span>
              {c.hp
                ? `HP ${c.hp}`
                : c.category === "能量"
                  ? "ENERGY"
                  : c.category === "宝可梦"
                    ? "HP待核对"
                    : subtypeLabels[c.subtype] || "TRAINER"}
            </span>
          </div>
          <div className="card-emblem">
            <div className="orbit" />
            <span>
              {c.category === "宝可梦" ? (
                <span className="ball-mark" />
              ) : c.category === "能量" ? (
                <Leaf size={38} strokeWidth={1} />
              ) : (
                <Layers size={38} strokeWidth={1} />
              )}
            </span>
          </div>
          <div className="face-name">{c.cnName}</div>
          <div className="face-foot">
            <span>{c.mark || "标记待核对"}</span>
            <span>资料占位 · 无原卡图</span>
          </div>
        </button>
        <div className="tile-info">
          <button className="text-link" onClick={() => setDetail(c)}>
            {c.cnName}
          </button>
          <button
            className={"icon-button heart " + (wish(c.printingId) ? "on" : "")}
            aria-label={
              (wish(c.printingId) ? "取消愿望 " : "加入愿望 ") + c.printingId
            }
            disabled={busy}
            onClick={() => act(() => toggleWish(c.printingId))}
          >
            <Heart size={17} />
          </button>
        </div>
        {c.image.url && (
          <small className="image-label">
            {failedImages.has(c.printingId)
              ? "卡图暂不可用 · 资料占位"
              : c.image.label + " · 替代展示"}
          </small>
        )}
        <div className="card-meta">
          {c.productCode || "基本能量储备"}{" "}
          {c.collectorNumber ? `· ${c.collectorNumber}` : "· 未指定版次"}
          {!!c.variants?.length && ` · ${c.variants.length} 种卡面`}
        </div>
        <div className="tile-actions">
          <span>
            持有 <b>{qty(c.printingId)}</b>
          </span>
          {building ? (
            <button
              onClick={() => changeDeck(c.printingId, 1)}
              aria-label={"加入卡组 " + c.printingId}
            >
              <Plus size={14} /> 加入卡组
            </button>
          ) : (
            <button
              disabled={busy}
              onClick={() => act(() => modifyHolding(c.printingId, 1))}
              aria-label={"收藏 " + c.printingId}
            >
              <Plus size={14} /> 收藏
            </button>
          )}
        </div>
      </article>
    );
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-ball" />
          <div>
            PTCG Lab<small>开源工具 · AI 对战</small>
          </div>
        </div>
        <div className="nav-caption">你的卡牌空间</div>
        <nav>
          {[
            {
              id: "cards",
              icon: Library,
              label: "卡牌资料库",
              n: cards.length,
            },
            {
              id: "collection",
              icon: Package,
              label: "我的收藏",
              n: quantityTotal,
            },
            { id: "decks", icon: Layers, label: "卡组构筑", n: decks.length },
            { id: "battle", icon: Swords, label: "人机练习", n: "AI" },
            { id: "sync", icon: ArrowDownToLine, label: "数据与对战更新", n: "" },
          ].map((n) => (
            <button
              key={n.id}
              className={page === n.id ? "active" : ""}
              onClick={() => {
                if (n.id === "collection" && page !== "collection") {
                  setQuery("");
                  setCategory("");
                  setProduct("");
                  setPack("");
                  setOwned("");
                  setElement("");
                  setEffect("");
                  setWishOnly(false);
                  setCardPage(0);
                }
                setPage(n.id);
                window.location.hash = n.id;
                setSelected([]);
              }}
            >
              <n.icon size={19} />
              <span>{n.label}</span>
              <small>{n.n}</small>
            </button>
          ))}
        </nav>
        <div className="side-notice">
          <span className="status-dot" /> 简中标准赛制
          <strong>G · H · I · J</strong>
          <small>规则快照 2026.09.16</small>
        </div>
        <div className="side-bottom">
          <div className="avatar">训</div>
          <div>
            本地训练家<small>数据保存在这台电脑</small>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span>
            工作空间 <ChevronRight size={14} />{" "}
            {page === "cards"
              ? "卡牌资料库"
              : page === "collection"
                ? "我的收藏"
                : page === "sync"
                  ? "数据与对战更新"
                : page === "battle"
                  ? "人机练习"
                  : "卡组构筑"}
          </span>
          <div>
            <span className="tiny-dot" /> 本地模式 <span className="vertical" />{" "}
            <span className="format-tag">简体中文 · PTCG</span>
          </div>
        </header>
        <main>
          {page === "sync" && <Sync onPublished={() => window.location.reload()} />}
          {error && (
            <div className="error-banner" role="alert">
              <AlertCircle size={18} />
              <span>{error}</span>
              <button onClick={() => setError("")} aria-label="关闭错误">
                <X size={16} />
              </button>
            </div>
          )}
          {page === "cards" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">YOUR NEXT DECK STARTS HERE</div>
                  <h1>
                    每一张卡，皆有可能<span>。</span>
                  </h1>
                  <p>发现卡牌，整理收藏，构筑属于你的对战思路。</p>
                </div>
                <button
                  className="primary"
                  onClick={() => act(() => createDeck("我的新卡组"))}
                >
                  <Plus size={17} /> 开始构筑
                </button>
              </div>
              <div className="overview">
                <div>
                  <BookOpen size={22} />
                  <span>
                    <strong>{cards.length}</strong>
                    <small>已收录卡牌身份</small>
                  </span>
                </div>
                <div>
                  <Package size={22} />
                  <span>
                    <strong>{quantityTotal}</strong>
                    <small>收藏与能量储备</small>
                  </span>
                </div>
                <div>
                  <Heart size={22} />
                  <span>
                    <strong>{wishCount}</strong>
                    <small>愿望清单</small>
                  </span>
                </div>
                <p>
                  从卡池出发，认真构筑。
                  <small>
                    已发售简中卡包与对战预组；繁中卡图会单独标注。
                  </small>
                </p>
              </div>
              <section className="catalog">
                {filters}
                <div className="section-line">
                  <span>
                    找到 <b>{visible.length}</b> 个卡牌身份
                  </span>
                  <span>已收录 ≠ 可对战 · 效果验证独立标记</span>
                </div>
                {pagination}
                <div className="cards-grid">
                  {pageCards.map((c) => renderCardTile(c))}
                </div>
                {pagination}
                {!visible.length && (
                  <div className="empty">
                    没有匹配的卡牌。试试中文名、英文名或编号。
                  </div>
                )}
              </section>
            </>
          )}
          {page === "collection" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">A COLLECTION THAT'S YOURS</div>
                  <h1>
                    让收藏，井然有序<span>。</span>
                  </h1>
                  <p>
                    按印刷版本与品相管理。基本能量储备单独标记，随时查看缺卡。
                  </p>
                </div>
                <div className="actions">
                  <a className="secondary" href="/api/collection/export">
                    <ArrowDownToLine size={16} /> 导出 CSV
                  </a>
                  <button
                    className="primary"
                    onClick={() => setImportKind("collection")}
                  >
                    <Upload size={16} /> 导入收藏
                  </button>
                </div>
              </div>
              {filters}
              <div className="collection-tools">
                <div className="segmented">
                  <button
                    className={!wishOnly ? "active" : ""}
                    onClick={() => setWishOnly(false)}
                  >
                    全部收藏
                  </button>
                  <button
                    className={wishOnly ? "active" : ""}
                    onClick={() => setWishOnly(true)}
                  >
                    愿望清单 {wishCount}
                  </button>
                </div>
                <button
                  className="text-link"
                  onClick={() =>
                    act(async () =>
                      setBatches(await api("/collection/imports")),
                    )
                  }
                >
                  <History size={15} /> 导入记录
                </button>
              </div>
              {selected.length > 0 && (
                <div className="batch-bar">
                  已选 {selected.length} 个身份{" "}
                  <button
                    onClick={() =>
                      act(async () => {
                        await api(
                          "/collection",
                          {
                            expectedVersion: collection.version,
                            changes: selected.map((id) => ({
                              ...holding(id),
                              quantity: holding(id).quantity + 1,
                            })),
                          },
                          "PUT",
                        );
                        await refreshCollection();
                        setSelected([]);
                      })
                    }
                  >
                    各增加1张（未标注品相）
                  </button>
                  <button
                    disabled={
                      busy || !selected.some((id) => holding(id).quantity > 0)
                    }
                    onClick={() =>
                      act(async () => {
                        await api(
                          "/collection",
                          {
                            expectedVersion: collection.version,
                            changes: selected
                              .filter((id) => holding(id).quantity > 0)
                              .map((id) => ({
                                ...holding(id),
                                quantity: holding(id).quantity - 1,
                              })),
                          },
                          "PUT",
                        );
                        await refreshCollection();
                        setSelected([]);
                      })
                    }
                  >
                    各减少1张（未标注品相）
                  </button>
                  <button onClick={() => setSelected([])}>取消选择</button>
                </div>
              )}
              {pagination}
              <div className="collection-table">
                <div className="table-header">
                  <span />
                  <span>卡牌 / 印刷版本</span>
                  <span>类别</span>
                  <span>持有数量</span>
                  <span>操作</span>
                </div>
                {pageCards.map((c) => (
                  <div className="table-row" key={c.printingId}>
                    <input
                      type="checkbox"
                      aria-label={"选择 " + c.printingId}
                      checked={selected.includes(c.printingId)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, c.printingId]
                            : selected.filter((x) => x !== c.printingId),
                        )
                      }
                    />
                    <button className="row-card" onClick={() => setDetail(c)}>
                      {badge(c)}
                      <span>
                        <b>{c.cnName}</b>
                        <small>
                          {c.productCode || "储备 · 未指定版次"}{" "}
                          {c.collectorNumber}
                        </small>
                      </span>
                    </button>
                    <span>{c.category}</span>
                    <strong>{qty(c.printingId)}</strong>
                    <button
                      className="secondary small"
                      onClick={() => setDetail(c)}
                    >
                      管理
                    </button>
                  </div>
                ))}
                {!collectionCards.length && (
                  <div className="empty">
                    <Package size={32} />
                    <h3>收藏从第一张卡开始</h3>
                    <p>在卡牌库点击“收藏”，或导入 CSV 批量添加。</p>
                    <button
                      className="secondary"
                      onClick={() => setPage("cards")}
                    >
                      浏览卡牌库
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
          {page === "decks" && !draft && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">BUILD. REFINE. MAKE IT YOURS.</div>
                  <h1>
                    把灵感，构筑成型<span>。</span>
                  </h1>
                  <p>从一副预组出发，或自由探索新的可能。</p>
                </div>
                <button
                  className="primary"
                  onClick={() => act(() => createDeck("未命名卡组"))}
                >
                  <Plus size={16} /> 新建卡组
                </button>
              </div>
              <h2>
                从已验证的预组开始 <span className="label">60 CARDS</span>
              </h2>
              <div className="template-grid">
                {meta?.templates.map((t: any, i: number) => (
                  <button
                    className={"template template-" + i}
                    key={t.id}
                    onClick={() => act(() => createDeck(t.name, t.entries))}
                  >
                    <div className="eyebrow">{t.sourceUrl ? "TEST DECK" : "MASTER STRATEGY"} / 0{i + 1}</div>
                    <h2>{t.name.split(" · ")[0]}</h2>
                    <p>{t.description || "60张完整预组 · 三层校验通过"}</p>
                    {t.knownIssues && <p>{t.knownIssues}</p>}
                    <span>
                      以此创建卡组 <ArrowUpRight size={18} />
                    </span>
                    <div className="template-orbit" />
                  </button>
                ))}
              </div>
              <div className="section-line">
                <h2>
                  我的卡组 <span className="label">{decks.length}</span>
                </h2>
                <button
                  className="secondary"
                  onClick={() => setImportKind("deck")}
                >
                  <Upload size={16} /> 导入卡组
                </button>
              </div>
              <div className="deck-list">
                {decks.map((d) => (
                  <div className="deck-list-row" key={d.id}>
                  <button className="deck-open" onClick={() => act(() => openDeck(d))}>
                    <span className="deck-icon">
                      <Layers size={22} />
                    </span>
                    <span>
                      <b>{d.name}</b>
                      <small>
                        {count(d.entries)} 张 · 更新于{" "}
                        {new Date(d.updatedAt).toLocaleDateString("zh-CN")}
                      </small>
                    </span>
                    <ChevronRight size={18} />
                  </button>
                  <button className="deck-delete" aria-label={"删除卡组：" + d.name}
                    disabled={busy} onClick={() => act(() => deleteDeck(d))}>
                    <Trash2 size={17} /> 删除
                  </button>
                  </div>
                ))}
                {!decks.length && (
                  <div className="empty">
                    还没有卡组。从上方预组创建你的第一副卡组。
                  </div>
                )}
              </div>
            </>
          )}
          {page === "decks" && draft && (
            <>
              <div className="editor-heading">
                <button
                  className="text-link"
                  onClick={() =>
                    act(async () => {
                      await persist(draft);
                      setDraft(null);
                      await refreshDecks();
                    })
                  }
                >
                  ← 全部卡组
                </button>
                <div>
                  <input
                    aria-label="卡组名称"
                    value={draft.name}
                    maxLength={80}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value })
                    }
                  />
                  <span
                    className={
                      "save-state " + (saveStatus === "保存失败" ? "bad" : "")
                    }
                  >
                    <Check size={13} />
                    {saveStatus}
                  </span>
                </div>
                <div className="actions">
                  {saveStatus === "保存失败" && (
                    <button
                      className="primary small"
                      onClick={() => act(recoverDraft)}
                    >
                      将本页内容另存为副本
                    </button>
                  )}
                  <button
                    className="secondary small"
                    onClick={() =>
                      act(async () => {
                        await persist(draft);
                        await createDeck(draft.name + " · 副本", draft.entries);
                      })
                    }
                  >
                    <Copy size={15} /> 复制
                  </button>
                  <button
                    className="secondary small"
                    onClick={() => setImportKind("deck")}
                  >
                    <Upload size={15} /> 导入
                  </button>
                </div>
              </div>
              <div className="editor-layout">
                <section className="editor-library">
                  {filters}
                  <div className="section-line">
                    为当前卡组加入卡牌 <span>{visible.length} 个结果</span>
                  </div>
                  {pagination}
                  <div className="cards-grid compact">
                    {pageCards.map((c) => renderCardTile(c, true))}
                  </div>
                  {pagination}
                </section>
                <aside className="deck-panel">
                  <div className="deck-panel-top">
                    <span>当前构筑</span>
                    <strong
                      className={count(draft.entries) === 60 ? "good" : ""}
                    >
                      {count(draft.entries)}
                      <small>/ 60</small>
                    </strong>
                  </div>
                  <div className="progress">
                    <i
                      style={{
                        width: `${Math.min((count(draft.entries) / 60) * 100, 100)}%`,
                      }}
                    />
                  </div>
                  <div className="distribution">
                    {["宝可梦", "训练家", "能量"].map((c) => (
                      <span key={c}>
                        {c}
                        <b>{stats?.categories[c] || 0}</b>
                      </span>
                    ))}
                  </div>
                  <div className="deck-entries">
                    {["宝可梦", "训练家", "能量"].map((cat) => (
                      <React.Fragment key={cat}>
                        {draft.entries.some(
                          (e) => cardMap[e.printingId]?.category === cat,
                        ) && <h4>{cat}</h4>}
                        {draft.entries
                          .filter(
                            (e) => cardMap[e.printingId]?.category === cat,
                          )
                          .map((e) => (
                            <div className="deck-entry" key={e.printingId}>
                              <button
                                className="entry-name"
                                onClick={() => setDetail(cardMap[e.printingId])}
                              >
                                {badge(cardMap[e.printingId])}
                                <span>
                                  {cardMap[e.printingId]?.cnName}
                                  <small>
                                    {cardMap[e.printingId]?.productCode ||
                                      "储备"}{" "}
                                    {cardMap[e.printingId]?.collectorNumber}
                                  </small>
                                </span>
                              </button>
                              <div className="stepper">
                                <button
                                  aria-label={"卡组减少 " + e.printingId}
                                  onClick={() => changeDeck(e.printingId, -1)}
                                >
                                  <Minus size={12} />
                                </button>
                                <b>{e.quantity}</b>
                                <button
                                  aria-label={"卡组增加 " + e.printingId}
                                  onClick={() => changeDeck(e.printingId, 1)}
                                >
                                  <Plus size={12} />
                                </button>
                              </div>
                            </div>
                          ))}
                      </React.Fragment>
                    ))}
                    {!draft.entries.length && (
                      <div className="empty small-empty">
                        点击左侧卡牌，开始构筑。
                      </div>
                    )}
                  </div>
                  <section className="validation">
                    <h3>
                      构筑检查 <span className="label">3 LAYERS</span>
                    </h3>
                    <div className="check-pills">
                      <span
                        className={
                          check?.structure === "valid" ? "pass" : "fail"
                        }
                      >
                        结构 {check?.structure === "valid" ? "通过" : "待调整"}
                      </span>
                      <span
                        className={
                          check?.legality === "valid" ? "pass" : "fail"
                        }
                      >
                        赛制 {check?.legality === "valid" ? "通过" : "待调整"}
                      </span>
                      <span
                        className={
                          check?.engine === "verified" ? "pass" : "warn"
                        }
                      >
                        对局{" "}
                        {check?.engine === "verified" ? "效果已发布" : "待验证"}
                      </span>
                    </div>
                    {check?.issues.map((i: any) => (
                      <div className="issue" key={i.code}>
                        <AlertCircle size={14} />
                        <span>
                          {i.explanation}
                          <small>{i.suggestedFix}</small>
                          {i.cardRefs?.map((id: string) => (
                            <button
                              className="text-link"
                              key={id}
                              onClick={() => setDetail(cardMap[id])}
                            >
                              {cardMap[id]?.cnName}{" "}
                              {cardMap[id]?.collectorNumber}
                            </button>
                          ))}
                        </span>
                      </div>
                    ))}
                    {check?.playable && (
                      <div className="pass-line">
                        <CheckCircle2 size={16} /> 已发布效果支持 ·
                        可实验性自由对战
                      </div>
                    )}
                    <small className="muted">
                      赛制快照：2026.09.16 · 判定日：2026.09.30
                    </small>
                  </section>
                  <section className="missing">
                    <h3>
                      缺卡清单{" "}
                      <strong>
                        {missing?.totalMissing ?? 0}
                        <small> 张</small>
                      </strong>
                    </h3>
                    <select
                      aria-label="缺卡计算方式"
                      value={missingMode}
                      onChange={(e) => setMissingMode(e.target.value)}
                    >
                      <option value="exact">按指定印刷版本</option>
                      <option value="equivalent">接受已审核规则等价版本</option>
                    </select>
                    <details>
                      <summary>
                        查看明细 · 已持有 {missing?.ownedUsed || 0} /{" "}
                        {count(draft.entries)}
                      </summary>
                      {missing?.rows
                        .filter((r: any) => r.missing > 0)
                        .map((r: any) => (
                          <div className="missing-row" key={r.printingId}>
                            <span>{cardMap[r.printingId]?.cnName}</span>
                            <b>缺 {r.missing}</b>
                          </div>
                        ))}
                    </details>
                    <small className="muted">
                      未持有卡牌不影响保存与模拟资格。
                    </small>
                  </section>
                  <section className="opening">
                    <h3>起手观察</h3>
                    <p>
                      初始7张含基础宝可梦的概率{" "}
                      <b>
                        {stats?.openingBasicProbability === null
                          ? "—"
                          : (
                              (stats?.openingBasicProbability || 0) * 100
                            ).toFixed(1) + "%"}
                      </b>
                    </p>
                    <div className="seed">
                      <label>
                        随机种子
                        <input
                          aria-label="随机种子"
                          type="number"
                          value={seed}
                          onChange={(e) => setSeed(Number(e.target.value))}
                        />
                      </label>
                      <button
                        className="secondary small"
                        onClick={() => setSeed(seed + 1)}
                      >
                        <Shuffle size={14} /> 再抽一次
                      </button>
                    </div>
                    <div className="opening-cards">
                      {stats?.openingHand.map((id: string, i: number) => (
                        <span key={i}>{cardMap[id]?.cnName}</span>
                      ))}
                    </div>
                    <small className="muted">{stats?.openingNote}</small>
                    <details>
                      <summary>进化关系与招式费用</summary>
                      {stats?.evolutions.map((e: any, i: number) => (
                        <p key={i}>
                          {e.name} · {stageLabels[e.stage] || "进化信息待核对"}
                          {e.from.length
                            ? " ← " +
                              (cards.find((c) => c.englishName === e.from[0])
                                ?.cnName || e.from[0])
                            : ""}
                        </p>
                      ))}
                      {stats?.attackCosts.map((c: any, i: number) => (
                        <div className="cost-row" key={i}>
                          <b>{c.name}</b>
                          {c.attacks.map((a: any, j: number) => (
                            <small key={j}>
                              {a.name} ·{" "}
                              {a.cost
                                .map((t: string) => typeLabels[t] || t)
                                .join(" / ")}
                            </small>
                          ))}
                        </div>
                      ))}
                    </details>
                  </section>
                  <div className="editor-footer">
                    <button
                      className="primary"
                      disabled={busy || check?.legality !== "valid"}
                      onClick={() =>
                        act(async () => {
                          await persist(draft);
                          await api("/decks/" + draft.id + "/revisions", {
                            expectedVersion: versions.current.get(draft.id),
                          });
                          setToast("版本已保存；相同卡表复用已有版本");
                          setRevisions(
                            await api("/decks/" + draft.id + "/revisions"),
                          );
                        })
                      }
                    >
                      <Check size={16} /> 保存卡组版本
                    </button>
                    <div>
                      <button
                        onClick={() =>
                          act(async () => {
                            await persist(draft);
                            setRevisions(
                              await api("/decks/" + draft.id + "/revisions"),
                            );
                          })
                        }
                      >
                        <History size={14} /> 版本记录
                      </button>
                      <button
                        onClick={() =>
                          act(async () => {
                            await persist(draft);
                            window.location.href =
                              "/api/decks/" + draft.id + "/export";
                          })
                        }
                      >
                        <ArrowDownToLine size={14} /> CSV
                      </button>
                      <button
                        onClick={() =>
                          act(async () => {
                            await persist(draft);
                            window.location.href =
                              "/api/decks/" + draft.id + "/export?format=text";
                          })
                        }
                      >
                        文本
                      </button>
                    </div>
                  </div>
                </aside>
              </div>
            </>
          )}
          {page === "battle" && (
            <Battle
              cards={cards}
              onBuild={() => {
                setPage("decks");
                window.location.hash = "decks";
              }}
            />
          )}
        </main>
        <footer className="footer">
          为每一次认真构筑而作。<span>非官方工具 · 仅限本地管理与验证</span>
        </footer>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          {toast}
        </div>
      )}
      {detail && (
        <Modal title="卡牌详情与收藏" close={() => setDetail(null)}>
          <div className="detail-title">
            {badge(detail)}
            <div>
              <h2>{detail.cnName}</h2>
              <p>
                {detail.englishName} · {detail.productCode || "基本能量储备"}{" "}
                {detail.collectorNumber}
              </p>
            </div>
          </div>
          {detail.image.url && !failedImages.has(detail.printingId) && (
            <DetailImage
              key={detail.printingId}
              card={detail}
              onError={() =>
                setFailedImages((prev) => new Set(prev).add(detail.printingId))
              }
            />
          )}
          <div className="detail-facts">
            <span>资料已收录</span>
            <span>
              {detail.sourceVerified ? "资料已审核" : "资料待审核"} · 标记{" "}
              {detail.mark || "未确认"}
            </span>
            <span>
              {detail.effectStatus === "verified"
                ? "效果已验证 · 有限组合"
                : "效果待验证 · 暂不可对战"}
            </span>
          </div>
          <CardArtwork key={detail.printingId} id={detail.printingId} uploaded={detail.image.userUploaded} onChange={(card: Card) => {
            setCards(previous => previous.map(c => c.printingId === card.printingId ? { ...c, ...card } : c));
            setDetail(previous => previous?.printingId === card.printingId ? { ...previous, ...card } : previous);
            setFailedImages(previous => { const next = new Set(previous); next.delete(card.printingId); return next; });
            setToast("卡图已更新");
          }} />
          <CardDescription details={detail.chineseDetails} types={typeLabels} />
          {detail.catalogStatus === "source-conflict" && (
            <p className="notice" role="note">
              编号存在来源冲突：原站的同一编号指向不同卡牌，现分别保留资料，具体印刷尚待核对。
            </p>
          )}
          {detail.pendingSourceException && <p className="notice" role="note">待核实例外：保留原始资料，暂不开放对战，不计入已支持卡牌。</p>}
          {detail.identityKind === "source-record" && (
            <p className="muted">
              原表标记：{detail.printedNumber || "未提供编号"}。此条目按来源区分，不代表已经确认的正式卡号。
            </p>
          )}
          <h3>规则核对摘要</h3>
          <p className="rules-text">{detail.reviewNote}</p>
          <small className="muted">
            此处为核对摘要，并非完整官方卡面文字。
          </small>
          <details>
            <summary>资料来源与印刷信息</summary>
            <p>{detail.printingId}</p>
            {detail.sourceEvidence.map((url, i) => (
              <a
                className="source"
                target="_blank"
                rel="noreferrer"
                key={i}
                href={url}
              >
                查看来源 {i + 1} <ArrowUpRight size={13} />
              </a>
            ))}
          </details>
          <hr />
          <h3>
            {detail.identityKind === "basic-energy"
              ? "能量储备 · 未指定收藏版次"
              : "我的收藏"}
          </h3>
          {detail.identityKind === "basic-energy" && (
            <p className="notice">
              可登记用于构筑的基本能量数量；这不代表已确认某个收藏印刷版本。
            </p>
          )}
          <HoldingEditor
            key={detail.printingId + ":" + collection.version}
            card={detail}
            holdings={collection.entries.filter(
              (e) => e.printingId === detail.printingId,
            )}
            busy={busy}
            save={(r) => act(() => saveHold(r))}
          />
          <CardVariants id={detail.printingId} version={collection.version} onChange={refreshCollection} />
          <button
            className={
              "secondary wish-button " + (wish(detail.printingId) ? "on" : "")
            }
            disabled={busy}
            onClick={() => act(() => toggleWish(detail.printingId))}
          >
            <Heart size={16} />
            {wish(detail.printingId)
              ? "已在愿望清单 · 点击移除"
              : "加入愿望清单"}
          </button>
          {draft && (
            <button
              className="primary"
              onClick={() => {
                changeDeck(detail.printingId, 1);
                setToast("已加入当前卡组");
              }}
            >
              <Plus size={16} /> 加入 {draft.name}
            </button>
          )}
        </Modal>
      )}
      {importKind && (
        <ImportModal
          kind={importKind}
          cards={cards}
          close={() => setImportKind(null)}
          apply={async (text, resolutions, preview) => {
            if (importKind === "collection") {
              const r = await api("/collection/imports", {
                text,
                kind: "collection",
                resolutions,
                expectedVersion: collection.version,
              });
              await refreshCollection();
              setToast(
                r.duplicate ? "此批次已导入，没有重复增加" : "收藏导入完成",
              );
            } else if (draft) {
              setDraft({ ...draft, entries: preview.entries });
              setToast("已替换当前草稿，正在自动保存");
            } else await createDeck("导入的卡组", preview.entries);
            setImportKind(null);
          }}
        />
      )}
      {revisions && (
        <Modal title="不可变卡组版本" close={() => setRevisions(null)}>
          {!revisions.length && (
            <p>
              暂无可用版本。保存当前卡表可创建版本，或恢复相同卡表的已删除版本。
            </p>
          )}
          {revisions.map((r: any) => (
            <article className="revision" key={r.id}>
              <h3>
                版本 {r.number}
                <span>{count(r.entries)} 张</span>
              </h3>
              <p>
                {new Date(r.createdAt).toLocaleString("zh-CN")} ·{" "}
                卡表快照 · 开局前自动校验当前规则
              </p>
              {(
                <button
                  className="primary"
                  onClick={() => {
                    sessionStorage.setItem("ptcg-preferred-revision", r.id);
                    localStorage.removeItem("ptcg-match");
                    setRevisions(null);
                    setPage("battle");
                    window.location.hash = "battle";
                  }}
                >
                  使用此版本对战
                </button>
              )}
              <code>{r.hash.slice(0, 20)}…</code>
              <p className="muted">
                相对当前草稿：{revisionDiff(r.entries, draft?.entries || [])}{" "}
                张调整
              </p>
              <button
                className="secondary"
                onClick={() =>
                  act(async () => {
                    await createDeck(
                      (draft?.name || "卡组") + " · 版本" + r.number,
                      r.entries,
                    );
                    setRevisions(null);
                  })
                }
              >
                从此版本创建副本
              </button>
              <button className="secondary" disabled={busy || !draft}
                onClick={() => {
                  if (!draft || !window.confirm(`删除版本 ${r.number}？此版本将从列表移除，已有对局和回放保留。再次保存相同卡表可恢复此版本。`)) return;
                  void act(async () => {
                    await api(`/decks/${draft.id}/revisions/${r.id}`, {expectedVersion: draft.version}, "DELETE");
                    setRevisions(await api(`/decks/${draft.id}/revisions`));
                    if (sessionStorage.getItem("ptcg-preferred-revision") === r.id)
                      sessionStorage.removeItem("ptcg-preferred-revision");
                    setToast("版本已删除，历史对局已保留");
                  });
                }}>删除版本 {r.number}</button>
            </article>
          ))}
        </Modal>
      )}
      {batches && (
        <Modal title="收藏导入记录" close={() => setBatches(null)}>
          {!batches.length && <p>暂无导入记录。</p>}
          {batches.map((b) => (
            <article className="revision" key={b.id}>
              <h3>
                {b.quantity} 张卡牌{" "}
                <span>{b.status === "applied" ? "已导入" : "已撤销"}</span>
              </h3>
              <p>{new Date(b.createdAt).toLocaleString("zh-CN")}</p>
              <button
                className="secondary"
                disabled={busy || b.status === "undone"}
                onClick={() =>
                  act(async () => {
                    await api("/collection/imports/" + b.id + "/undo", {
                      expectedVersion: collection.version,
                    });
                    await refreshCollection();
                    setBatches(await api("/collection/imports"));
                    setToast("导入已撤销");
                  })
                }
              >
                撤销此批次
              </button>
            </article>
          ))}
        </Modal>
      )}
    </div>
  );
}
function revisionDiff(a: Entry[], b: Entry[]) {
  const ids = new Set([...a, ...b].map((e) => e.printingId));
  return [...ids].reduce(
    (s, id) =>
      s +
      Math.abs(
        (a.find((e) => e.printingId === id)?.quantity || 0) -
          (b.find((e) => e.printingId === id)?.quantity || 0),
      ),
    0,
  );
}
function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = ref.current;
    const { scrollX, scrollY } = window;
    const opener = document.activeElement as HTMLElement | null;
    const root = document.documentElement;
    const overflow = root.style.overflow;
    root.style.overflow = "hidden";
    dialog?.showModal();
    window.scrollTo(scrollX, scrollY);
    return () => {
      dialog?.close();
      root.style.overflow = overflow;
      opener?.focus({ preventScroll: true });
      window.scrollTo(scrollX, scrollY);
    };
  }, []);
  return (
    <dialog ref={ref} className="modal" onCancel={close}>
      <div className="modal-head">
        <h2>{title}</h2>
        <button className="icon-button" onClick={close} aria-label="关闭窗口">
          <X size={20} />
        </button>
      </div>
      <div className="modal-content">{children}</div>
    </dialog>
  );
}
function HoldingEditor({
  card,
  holdings,
  busy,
  save,
}: {
  card: Card;
  holdings: Holding[];
  busy: boolean;
  save: (h: Holding) => void;
}) {
  const [condition, setCondition] = useState(
    holdings[0]?.condition || "未标注",
  );
  const initial = (c: string) =>
    holdings.find((h) => h.condition === c) || {
      printingId: card.printingId,
      condition: c,
      quantity: 0,
      notes: "",
      wishlist: false,
    };
  const [row, setRow] = useState<Holding>(initial(condition));
  return (
    <div className="holding-editor">
      <label>
        品相
        <select
          aria-label="收藏品相"
          value={condition}
          onChange={(e) => {
            setCondition(e.target.value);
            setRow(initial(e.target.value));
          }}
        >
          {["未标注", "全新", "良好", "使用痕迹"].map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </label>
      <label>
        持有数量
        <input
          aria-label="收藏数量"
          type="number"
          min="0"
          max="9999"
          value={row.quantity}
          onChange={(e) => setRow({ ...row, quantity: Number(e.target.value) })}
        />
      </label>
      <label className="full">
        备注
        <input
          aria-label="收藏备注"
          placeholder="来源、整理位置或收藏备注"
          maxLength={500}
          value={row.notes}
          onChange={(e) => setRow({ ...row, notes: e.target.value })}
        />
      </label>
      <button
        className="primary full"
        disabled={busy}
        onClick={() => save(row)}
      >
        保存收藏
      </button>
      <small className="full muted">
        {holdings.map((h) => `${h.condition} ${h.quantity}张`).join(" · ") ||
          "暂无收藏记录"}
      </small>
    </div>
  );
}
function ImportModal({
  kind,
  cards,
  close,
  apply,
}: {
  kind: string;
  cards: Card[];
  close: () => void;
  apply: (
    text: string,
    resolutions: Record<string, string>,
    preview: any,
  ) => Promise<void>;
}) {
  const [text, setText] = useState(""),
    [resolutions, setResolutions] = useState<Record<string, string>>({}),
    [preview, setPreview] = useState<any>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function inspect(value = text, res = resolutions) {
    setBusy(true);
    setError("");
    try {
      setPreview(
        await api("/import-preview", { text: value, kind, resolutions: res }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={
        kind === "deck" ? "导入卡组 · 先确认版本" : "导入收藏 · 预览后添加"
      }
      close={close}
    >
      <p>
        支持 CSV 或“数量 卡名 系列 编号”文本。同名存在多个版本时，请逐行确认。
        {kind === "deck"
          ? "确认后替换当前草稿。"
          : "收藏导入按数量增加，重复批次不会重复添加。"}
      </p>
      <label className="file-input">
        <Upload size={16} /> 选择 CSV / TXT 文件
        <input
          aria-label="选择导入文件"
          type="file"
          accept=".csv,.txt"
          onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f) {
              if (f.size > 200000) {
                setError("文件最多200KB");
                return;
              }
              const t = await f.text();
              setText(t);
              setResolutions({});
              setPreview(null);
            }
          }}
        />
      </label>
      <textarea
        aria-label="导入内容"
        rows={7}
        placeholder={
          "printingId,quantity\nCN:CSVM2cC:007,4\n\n或：4 赛富豪ex CSVM2cC 007"
        }
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setPreview(null);
          setResolutions({});
        }}
      />
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <button
        className="secondary"
        disabled={busy || !text.trim()}
        onClick={() => inspect()}
      >
        解析并预览
      </button>
      {preview && (
        <div className="import-preview">
          <h3>
            {preview.rows.length} 行 ·{" "}
            {preview.ready ? "可以导入" : "请先处理以下项目"}
          </h3>
          {preview.warnings.map((w: string, i: number) => (
            <p key={i} className="notice">
              {w}
            </p>
          ))}
          {preview.rows.map((r: any) => (
            <div className="import-row" key={r.row}>
              <span>
                {r.quantity} × {r.name}
              </span>
              {r.candidates.length > 1 ? (
                <select
                  aria-label={"确认第" + (r.row + 1) + "行版本"}
                  value={r.printingId || ""}
                  onChange={(e) => {
                    const next = {
                      ...resolutions,
                      [String(r.row)]: e.target.value,
                    };
                    setResolutions(next);
                    inspect(text, next);
                  }}
                >
                  <option value="" disabled>
                    选择具体印刷版本
                  </option>
                  {r.candidates.map((id: string) => {
                    const c = cards.find((c) => c.printingId === id)!;
                    return (
                      <option key={id} value={id}>
                        {c.cnName} · {c.productCode} {c.collectorNumber}
                      </option>
                    );
                  })}
                </select>
              ) : (
                <small>{r.printingId}</small>
              )}
              {r.error && <small className="inline-error">{r.error}</small>}
            </div>
          ))}
          <button
            className="primary"
            disabled={busy || !preview.ready}
            onClick={async () => {
              setBusy(true);
              try {
                await apply(text, resolutions, preview);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            确认导入 {count(preview.entries)} 张
          </button>
        </div>
      )}
    </Modal>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
