import { useEffect, useState } from "react";
import { platformServices } from "@ptcg/platform-services";
import { CardDescription } from "../../web/src/CardDescription";
import { Artwork } from "./Artwork";
import { Network } from "./Network";
import { CollectionTools, CollectionEditor, file } from "./CollectionTools";
import { ResourceSettings } from "./ResourceSettings";
import {
  message,
  type Card,
  type Collection,
  type Deck,
  type Entry,
  type MobileOwner,
  type Source,
} from "./contracts";
import "./mobile.css";

const services = platformServices("ios");
const energy = {
  GRASS: "草",
  FIRE: "火",
  WATER: "水",
  LIGHTNING: "雷",
  PSYCHIC: "超",
  FIGHTING: "斗",
  DARK: "恶",
  METAL: "钢",
  DRAGON: "龙",
  COLORLESS: "无色",
  FAIRY: "妖",
};
type Page = "cards" | "collection" | "decks" | "online" | "settings";
const emptySource: Source = {
  source: "github-raw",
  baseUrl: "",
  capacityMiB: 512,
};
const total = (entries: Entry[]) =>
  entries.reduce((sum, row) => sum + row.quantity, 0);

export function App({ owner }: { owner: MobileOwner }) {
  const [page, setPage] = useState<Page>("cards"),
    [cards, setCards] = useState<Card[]>([]),
    [count, setCount] = useState(0);
  const [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0),
    [catalogVersion, setCatalogVersion] = useState("");
  const [collection, setCollection] = useState<Collection>({
      version: 0,
      entries: [],
    }),
    [decks, setDecks] = useState<Deck[]>([]);
  const [draft, setDraft] = useState<Deck | null>(null),
    [saved, setSaved] = useState(""),
    [selected, setSelected] = useState<Card | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [ready, setReady] = useState(false);
  const [source, setSource] = useState<Source>(emptySource),
    [cacheBytes, setCacheBytes] = useState(0),
    [imageEpoch, setImageEpoch] = useState(0);
  const [importText, setImportText] = useState(""),
    [exportText, setExportText] = useState("");
  const [deckQuery, setDeckQuery] = useState(""),
    [exportFormat, setExportFormat] = useState("csv"),
    [deleteTarget, setDeleteTarget] = useState<Deck | null>(null);
  const [revisions, setRevisions] = useState<
    {
      id: string;
      catalogVersion: string;
      entries: Entry[];
      contentHash: string;
    }[]
  >([]);
  const loadRevisions = async (id: string) =>
    setRevisions(
      (
        await owner.request<{ revisions: typeof revisions }>({
          op: "revisions",
          deckId: id,
        })
      ).revisions,
    );
  useEffect(() => {
    setRevisions([]);
    if (draft) void loadRevisions(draft.id).catch((e) => setError(message(e)));
  }, [owner, draft?.id]);
  const dirty = !!draft && JSON.stringify(draft) !== saved;
  const refresh = async () => {
    const [capabilities, holds, rows, artwork] = await Promise.all([
      owner.request<{
        catalogVersion: string;
        implemented: { localPractice: boolean; localRuleVerification: boolean };
      }>({ op: "capabilities" }),
      owner.request<Collection>({ op: "collection" }),
      owner.request<Deck[]>({ op: "decks" }),
      owner.request<{ settings: Source; cachedBytes: number }>({
        op: "artwork-settings",
      }),
    ]);
    if (
      capabilities.implemented.localPractice ||
      capabilities.implemented.localRuleVerification
    )
      throw new Error("移动端能力配置不匹配");
    setCatalogVersion(capabilities.catalogVersion);
    setCollection(holds);
    setDecks(rows);
    setSource(artwork.settings);
    setCacheBytes(artwork.cachedBytes);
    setReady(true);
  };
  useEffect(() => {
    let active = true;
    void refresh().catch((e) => {
      if (active) setError(message(e));
    });
    return () => {
      active = false;
    };
  }, [owner]);
  useEffect(() => {
    const resume = () => {
      if (document.visibilityState === "visible")
        void refresh()
          .then(() => setImageEpoch((n) => n + 1))
          .catch((e) => setError(message(e)));
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("pageshow", resume);
    return () => {
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("pageshow", resume);
    };
  }, [owner]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    const timer = setTimeout(() => {
      void owner
        .request<{ cards: Card[]; total: number }>({
          op: "catalog",
          query,
          offset,
          limit: 24,
          ownedOnly: page === "collection",
        })
        .then((result) => {
          if (active) {
            setCards(result.cards);
            setCount(result.total);
          }
        })
        .catch((e) => {
          if (active) setError(message(e));
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 180);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [owner, query, offset, page, collection.version, catalogVersion]);
  const act = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const edit = (deck: Deck) => {
    setDraft(structuredClone(deck));
    setSaved(JSON.stringify(deck));
    setExportText("");
    setDeleteTarget(null);
    setDeckQuery("");
    setPage("decks");
  };
  const save = async () => {
    if (!draft) return;
    const deck = await owner.request<Deck>({
      op: "save-deck",
      id: draft.id,
      name: draft.name,
      entries: draft.entries,
      expectedVersion: draft.version,
    });
    edit(deck);
    setDecks((rows) => [deck, ...rows.filter((r) => r.id !== deck.id)]);
    setNotice("卡组已保存到这台设备");
  };
  const add = (card: Card) => {
    if (!draft) {
      setNotice("请先在“卡组”中建立或选择一个卡组");
      return;
    }
    setDraft({
      ...draft,
      entries: draft.entries.some((e) => e.printingId === card.printingId)
        ? draft.entries.map((e) =>
            e.printingId === card.printingId
              ? { ...e, quantity: Math.min(60, e.quantity + 1) }
              : e,
          )
        : [...draft.entries, { printingId: card.printingId, quantity: 1 }],
    });
    setNotice(`已将${card.cnName}加入草稿，记得保存`);
  };
  const holding = async (card: Card, quantity: number, wishlist: boolean) => {
    setCollection(
      await owner.request<Collection>({
        op: "set-holding",
        printingId: card.printingId,
        quantity,
        wishlist,
        expectedVersion: collection.version,
      }),
    );
  };
  return (
    <div className="mobile-product" data-platform={services.platform}>
      <header className="mobile-header">
        <div className="mobile-brand">
          <span className="master-ball" aria-hidden="true">
            M
          </span>
          <div>
            <strong>PTCG Lab</strong>
            <small>你的卡牌随行手册</small>
          </div>
        </div>
        <span className="offline-badge">本机资料</span>
      </header>
      <main>
        <div className="mobile-page-heading">
          <p className="eyebrow">
            {
              {
                online: "PLAY TOGETHER",
                cards: "CARD LIBRARY",
                collection: "YOUR COLLECTION",
                decks: "DECK WORKSHOP",
                settings: "PREFERENCES",
              }[page]
            }
          </p>
          <h1>
            {
              {
                online: "网络对战",
                cards: "卡牌资料库",
                collection: "我的收藏",
                decks: "我的卡组",
                settings: "资源与设置",
              }[page]
            }
          </h1>
          <p>
            {
              {
                online: "选择已保存的卡组，用房间码邀请朋友。",
                cards: "轻触卡牌，查看完整中文说明。",
                collection: "记录持有数量，留下想收集的卡牌。",
                decks: "从卡库选牌，保存属于你的组合。",
                settings: "选择图源、更新展示资料，管理这台设备上的数据。",
              }[page]
            }
          </p>
        </div>
        {error && (
          <div className="mobile-error" role="alert">
            {error}
            <button onClick={() => void act(refresh)}>重新加载</button>
          </div>
        )}
        {notice && (
          <p className="mobile-notice" role="status">
            {notice}
          </p>
        )}
        {!ready && !error && <p role="status">正在读取本机资料…</p>}
        {(page === "cards" || page === "collection") && (
          <>
            {page === "collection" && (
              <CollectionTools
                owner={owner}
                collection={collection}
                onChange={setCollection}
              />
            )}
            <label className="mobile-search">
              查找卡牌
              <input
                type="search"
                placeholder="卡名、卡号或卡包"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setOffset(0);
                }}
              />
            </label>
            <div className="mobile-list-status" aria-live="polite">
              <span>
                {loading ? "正在查找…" : `共 ${count.toLocaleString()} 张卡牌`}
              </span>
              {draft && (
                <button onClick={() => setPage("decks")}>
                  {draft.name} · {total(draft.entries)} 张
                  {dirty ? " · 待保存" : ""}
                </button>
              )}
            </div>
            <section
              className="mobile-card-grid"
              aria-label={page === "collection" ? "收藏卡牌" : "卡牌列表"}
            >
              {cards.map((card) => {
                const owned = collection.entries.find(
                  (r) =>
                    r.printingId === card.printingId &&
                    r.condition === "未标注",
                );
                const ownedTotal = collection.entries
                  .filter((r) => r.printingId === card.printingId)
                  .reduce((n, r) => n + r.quantity, 0);
                return (
                  <article key={card.printingId} className="mobile-card-tile">
                    <button
                      className="mobile-card-open"
                      onClick={() => setSelected(card)}
                      aria-label={`查看${card.cnName} ${card.printingId}`}
                    >
                      <Artwork
                        key={`${imageEpoch}:${card.printingId}`}
                        owner={owner}
                        id={card.printingId}
                        name={card.cnName}
                        enabled={card.hasArtwork}
                        interactive={false}
                      />
                      <strong>{card.cnName}</strong>
                      <small>
                        {card.productCode} · {card.collectorNumber}
                      </small>
                    </button>
                    <div className="mobile-card-actions">
                      <button
                        aria-label={`收藏${card.cnName}`}
                        disabled={busy}
                        onClick={() =>
                          void act(() =>
                            holding(
                              card,
                              (owned?.quantity || 0) + 1,
                              owned?.wishlist || false,
                            ),
                          )
                        }
                      >
                        持有 {ownedTotal} ＋
                      </button>
                      <button
                        aria-label={`加入卡组${card.cnName}`}
                        onClick={() => add(card)}
                        disabled={!draft}
                      >
                        ＋卡组
                      </button>
                    </div>
                  </article>
                );
              })}
            </section>
            {!loading && !cards.length && (
              <div className="mobile-empty">
                {page === "collection"
                  ? "还没有收藏，在卡牌资料库点击“持有 ＋”即可记录。"
                  : "没有找到符合条件的卡牌。"}
              </div>
            )}
            <div className="mobile-pagination">
              <button
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - 24))}
              >
                上一页
              </button>
              <span>
                {Math.floor(offset / 24) + 1} /{" "}
                {Math.max(1, Math.ceil(count / 24))}
              </span>
              <button
                disabled={offset + 24 >= count || loading}
                onClick={() => setOffset(offset + 24)}
              >
                下一页
              </button>
            </div>
          </>
        )}
        {page === "decks" && (
          <>
            <section className="mobile-panel" aria-label="导入卡组">
              <h2>带上已有卡组</h2>
              <p>
                直接选取“文件”中的 CSV、TXT 或
                JSON，也可以粘贴内容。桌面导出的卡组可直接使用；导入会新建卡组，不覆盖现有卡组。
              </p>
              <button
                className="primary mobile-wide"
                disabled={busy || !ready || dirty}
                onClick={() =>
                  void act(async () => {
                    const result = await file<{
                      text?: string;
                      cancelled?: boolean;
                    }>(owner, { op: "pick-deck" });
                    if (!result.cancelled) {
                      setImportText(result.text || "");
                      setNotice("已读取卡组文件，请核对下方内容后导入");
                    } else setNotice("已取消选择文件");
                  })
                }
              >
                选择卡组文件
              </button>
              <details open={!!importText}>
                <summary>导入卡组文本</summary>
                <p>
                  CSV 表头为 printingId,quantity；文本每行是“数量 印刷ID”；JSON
                  使用 name、entries。最多 128 KiB、500 行。
                </p>
                <textarea
                  aria-label="导入卡组文本"
                  rows={6}
                  maxLength={131072}
                  value={importText}
                  onChange={(e) => setImportText(e.target.value)}
                />
                <button
                  className="primary"
                  disabled={busy || !ready || !importText.trim() || dirty}
                  onClick={() =>
                    void act(async () => {
                      const deck = await owner.request<Deck>({
                        op: "import-deck",
                        text: importText,
                      });
                      edit(deck);
                      setDecks((rows) => [deck, ...rows]);
                      setImportText("");
                      setNotice(
                        `已导入 ${total(deck.entries)} 张卡牌，可修改卡组名称后保存`,
                      );
                    })
                  }
                >
                  检查并新建卡组
                </button>
              </details>
              {dirty && (
                <p>请先保存卡组或重新载入已保存版本，再导入其他卡组。</p>
              )}
            </section>
            <button
              className="primary mobile-wide"
              disabled={busy || !ready || dirty}
              onClick={() =>
                void act(async () => {
                  const deck = await owner.request<Deck>({
                    op: "save-deck",
                    id: null,
                    name: "我的新卡组",
                    entries: [],
                    expectedVersion: null,
                  });
                  edit(deck);
                  setDecks((rows) => [deck, ...rows]);
                })
              }
            >
              ＋ 新建卡组
            </button>
            {decks.length > 0 && (
              <label>
                查找卡组 · {decks.length} 个
                <input
                  type="search"
                  aria-label="查找卡组"
                  placeholder="按卡组名称查找"
                  value={deckQuery}
                  onChange={(e) => setDeckQuery(e.target.value)}
                />
              </label>
            )}
            <div className="mobile-deck-list">
              {decks
                .filter((deck) =>
                  deck.name
                    .toLocaleLowerCase()
                    .includes(deckQuery.trim().toLocaleLowerCase()),
                )
                .map((deck) => (
                  <button
                    key={deck.id}
                    disabled={busy || dirty || !!deleteTarget}
                    aria-pressed={draft?.id === deck.id}
                    onClick={() => edit(deck)}
                  >
                    <strong>{deck.name}</strong>
                    <span>
                      {total(deck.entries)} 张 · 版本 {deck.version}
                    </span>
                  </button>
                ))}
            </div>
            {!decks.length && (
              <div className="mobile-empty">
                还没有卡组。选择上方的卡组文件，或新建一个卡组开始选牌。
              </div>
            )}
            {draft && (
              <section className="mobile-panel" aria-label="编辑卡组">
                <label>
                  卡组名称
                  <input
                    maxLength={80}
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value })
                    }
                  />
                </label>
                <div className="mobile-list-status">
                  <strong>{total(draft.entries)} / 60 张</strong>
                  <span>{dirty ? "有未保存的修改" : "已保存"}</span>
                </div>
                {draft.entries.map((entry) => (
                  <div className="mobile-entry" key={entry.printingId}>
                    <button
                      onClick={() =>
                        void act(async () =>
                          setSelected(
                            await owner.request<Card>({
                              op: "card",
                              printingId: entry.printingId,
                            }),
                          ),
                        )
                      }
                    >
                      {entry.printingId}
                    </button>
                    <label>
                      数量
                      <input
                        aria-label={`${entry.printingId}数量`}
                        type="number"
                        inputMode="numeric"
                        min={0}
                        max={60}
                        value={entry.quantity}
                        onChange={(e) => {
                          const quantity = Math.max(
                            0,
                            Math.min(60, Number(e.target.value) || 0),
                          );
                          setDraft({
                            ...draft,
                            entries: draft.entries
                              .map((row) =>
                                row.printingId === entry.printingId
                                  ? { ...row, quantity }
                                  : row,
                              )
                              .filter((row) => row.quantity > 0),
                          });
                        }}
                      />
                    </label>
                  </div>
                ))}
                <button
                  className="mobile-wide"
                  onClick={() => {
                    setPage("cards");
                    setQuery("");
                    setOffset(0);
                  }}
                >
                  去卡库选牌
                </button>
                <div className="mobile-button-row">
                  <button
                    className="primary"
                    disabled={busy || !dirty}
                    onClick={() => void act(save)}
                  >
                    保存卡组
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        const rows = await owner.request<Deck[]>({
                          op: "decks",
                        });
                        const current = rows.find((row) => row.id === draft.id);
                        if (!current)
                          throw {
                            message: "原卡组已删除，请将草稿另存为新卡组。",
                          };
                        setDecks(rows);
                        edit(current);
                        setNotice("已载入保存版本，未保存的草稿修改已放弃");
                      })
                    }
                  >
                    重新载入（放弃草稿）
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        const value = await owner.request<Deck>({
                          op: "save-deck",
                          id: null,
                          name: draft.name.slice(0, 70) + "（副本）",
                          entries: draft.entries,
                          expectedVersion: null,
                        });
                        edit(value);
                        setDecks((rows) => [value, ...rows]);
                        setNotice("已另存为独立卡组，原卡组保持不变");
                      })
                    }
                  >
                    将草稿另存为新卡组
                  </button>
                  <button
                    disabled={busy || dirty}
                    onClick={() =>
                      void act(async () => {
                        const result = await owner.request<{
                          validation: {
                            count: number;
                            structureReady: boolean;
                          };
                          id: string;
                        }>({
                          op: "freeze-deck",
                          id: draft.id,
                          expectedVersion: draft.version,
                        });
                        await loadRevisions(draft.id);
                        setNotice(
                          `版本已冻结：${result.validation.count} 张。${result.validation.structureReady ? "基础结构完整。" : "仍需补全 60 张及基础宝可梦。"}实际准入由对战服务器确认。`,
                        );
                      })
                    }
                  >
                    冻结版本
                  </button>
                </div>
                <div className="mobile-deck-delete">
                  {!deleteTarget ? (
                    <button
                      className="mobile-danger"
                      disabled={busy || dirty}
                      onClick={() => setDeleteTarget(structuredClone(draft))}
                    >
                      删除卡组
                    </button>
                  ) : (
                    <section
                      role="alertdialog"
                      aria-labelledby="delete-deck-title"
                      aria-describedby="delete-deck-description"
                    >
                      <h3 id="delete-deck-title">
                        删除“{deleteTarget.name}”？
                      </h3>
                      <p id="delete-deck-description">
                        将永久删除这个可编辑卡组，无法撤销。已冻结的对战版本与历史对局仍保留。
                      </p>
                      <div className="mobile-button-row">
                        <button
                          autoFocus
                          disabled={busy}
                          onClick={() => setDeleteTarget(null)}
                        >
                          取消删除
                        </button>
                        <button
                          className="mobile-danger"
                          disabled={busy || dirty}
                          onClick={() =>
                            void act(async () => {
                              await owner.request({
                                op: "delete-deck",
                                id: deleteTarget.id,
                                expectedVersion: deleteTarget.version,
                              });
                              setDecks((rows) =>
                                rows.filter(
                                  (row) => row.id !== deleteTarget.id,
                                ),
                              );
                              setDraft(null);
                              setSaved("");
                              setRevisions([]);
                              setExportText("");
                              setDeleteTarget(null);
                              setNotice("卡组已删除，冻结版本和历史对局已保留");
                            })
                          }
                        >
                          确认删除卡组
                        </button>
                      </div>
                    </section>
                  )}
                  {dirty && <p>保存或放弃当前草稿后，可删除卡组。</p>}
                </div>
                <label>
                  卡组文件格式
                  <select
                    aria-label="卡组文件格式"
                    value={exportFormat}
                    onChange={(e) => {
                      setExportFormat(e.target.value);
                      setExportText("");
                    }}
                  >
                    <option value="csv">CSV · 桌面与手机通用</option>
                    <option value="text">TXT · 数量与印刷 ID</option>
                    <option value="json">JSON · 保留名称及目录版本</option>
                  </select>
                </label>
                <button
                  className="mobile-wide"
                  disabled={busy || dirty}
                  onClick={() =>
                    void act(async () => {
                      const document = await owner.request<{ text?: string }>({
                        op: "export-deck",
                        id: draft.id,
                        format: exportFormat,
                      });
                      setExportText(
                        document.text ?? JSON.stringify(document, null, 2),
                      );
                    })
                  }
                >
                  查看卡组导出文本
                </button>
                <button
                  disabled={busy || dirty}
                  onClick={() =>
                    void act(async () => {
                      const result = await file<{ cancelled?: boolean }>(
                        owner,
                        {
                          op: "share-deck",
                          id: draft.id,
                          format: exportFormat,
                        },
                      );
                      setNotice(
                        result.cancelled ? "已取消分享" : "卡组已通过系统分享",
                      );
                    })
                  }
                >
                  分享卡组文件
                </button>
                {exportText && (
                  <label>
                    复制此卡组内容到另一台设备
                    <textarea
                      readOnly
                      rows={7}
                      value={exportText}
                      onFocus={(e) => e.target.select()}
                    />
                  </label>
                )}
              </section>
            )}
            {draft && revisions.length > 0 && (
              <details className="mobile-panel">
                <summary>冻结版本（{revisions.length}）</summary>
                {revisions.map((revision) => (
                  <div className="mobile-revision" key={revision.id}>
                    <strong>{total(revision.entries)} 张 · 已保存快照</strong>
                    <small>目录：{revision.catalogVersion.slice(0, 12)}…</small>
                    <code>{revision.contentHash}</code>
                  </div>
                ))}
              </details>
            )}
          </>
        )}
        {page === "online" && <Network owner={owner} />}
        {page === "settings" && (
          <>
            <ResourceSettings
              owner={owner}
              blocked={dirty}
              onApplied={async () => {
                setSelected(null);
                setOffset(0);
                setImageEpoch((n) => n + 1);
                await refresh();
              }}
            />
            <section className="mobile-panel">
              <h2>图片来源与缓存</h2>
              <p>卡图由原生层按需获取，已缓存的图片断网后仍可查看。</p>
              <label>
                在线图源
                <select
                  value={source.source}
                  onChange={(e) =>
                    setSource({
                      ...source,
                      source: e.target.value as Source["source"],
                    })
                  }
                >
                  <option value="github-raw">GitHub Raw</option>
                  <option value="cdn">静态图源 / CDN</option>
                </select>
              </label>
              {source.source === "cdn" && (
                <label>
                  HTTPS 基础地址
                  <input
                    type="url"
                    autoCapitalize="none"
                    autoCorrect="off"
                    placeholder="https://images.example.com"
                    value={source.baseUrl}
                    onChange={(e) =>
                      setSource({ ...source, baseUrl: e.target.value })
                    }
                  />
                </label>
              )}
              <label>
                缓存容量
                <select
                  value={source.capacityMiB}
                  onChange={(e) =>
                    setSource({
                      ...source,
                      capacityMiB: Number(e.target.value),
                    })
                  }
                >
                  {[64, 256, 512, 1024, 2048].map((size) => (
                    <option key={size} value={size}>
                      {size} MiB
                    </option>
                  ))}
                </select>
              </label>
              <p>
                已缓存 {(cacheBytes / 1024 / 1024).toFixed(1)}{" "}
                MiB。只下载实际查看的卡图，失败不会清空资料。
              </p>
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await owner.request<{
                      settings: Source;
                      cachedBytes: number;
                    }>({ op: "set-artwork-source", settings: source });
                    setSource(result.settings);
                    setCacheBytes(result.cachedBytes);
                    setImageEpoch((n) => n + 1);
                    setNotice("图片来源已保存");
                  })
                }
              >
                保存设置
              </button>
            </section>
            <section className="mobile-panel">
              <h2>本机资料</h2>
              <p>
                收藏、卡组和冻结版本保存在当前 App
                中。请保存草稿后再退出；系统终止不会清空已保存的数据。
              </p>
              <details>
                <summary>目录版本</summary>
                <code className="mobile-version">
                  {catalogVersion || "尚未读取"}
                </code>
              </details>
              <p>
                卡组 JSON 和收藏 CSV
                可通过系统文件选择与分享交换。展示资料更新独立于服务器规则，保留已保存的数据与身份。
              </p>
            </section>
          </>
        )}
      </main>
      <nav className="mobile-nav" aria-label="主导航">
        {(
          [
            ["cards", "▤", "卡库"],
            ["collection", "♡", "收藏"],
            ["decks", "▱", "卡组"],
            ["online", "↔", "对战"],
            ["settings", "⚙", "设置"],
          ] as const
        ).map(([key, icon, label]) => (
          <button
            key={key}
            aria-current={page === key ? "page" : undefined}
            onClick={() => {
              setPage(key);
              setOffset(0);
            }}
          >
            <span aria-hidden="true">{icon}</span>
            {label}
          </button>
        ))}
      </nav>
      {selected && (
        <div
          className="mobile-sheet-backdrop"
          onClick={() => setSelected(null)}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`${selected.cnName}详情`}
            className="mobile-sheet"
            onClick={(e) => e.stopPropagation()}
          >
            <header>
              <div>
                <small>{selected.printingId}</small>
                <h2>{selected.cnName}</h2>
              </div>
              <button
                aria-label="关闭卡牌详情"
                onClick={() => setSelected(null)}
              >
                关闭
              </button>
            </header>
            <Artwork
              key={`${imageEpoch}:${selected.printingId}`}
              owner={owner}
              id={selected.printingId}
              name={selected.cnName}
              enabled={selected.hasArtwork}
            />
            <p>
              {selected.category} · {selected.hp ? `HP ${selected.hp}` : ""}
            </p>
            <CardDescription details={selected.chineseDetails} types={energy} />
            <button disabled={!draft} onClick={() => add(selected)}>
              加入编辑中的卡组
            </button>
            <CollectionEditor
              key={selected.printingId}
              owner={owner}
              printingId={selected.printingId}
              collection={collection}
              onChange={setCollection}
            />
          </section>
        </div>
      )}
    </div>
  );
}
