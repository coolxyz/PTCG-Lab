import { useEffect, useState } from "react";
import {
  conditions,
  message,
  type Collection,
  type Holding,
  type MobileOwner,
} from "./contracts";
const file = async <T,>(
  owner: MobileOwner,
  request: Record<string, unknown>,
) => {
  if (!owner.files) throw { message: "请在 iOS App 中使用系统文件功能。" };
  return owner.files<T>(request);
};
export { file };
type Batch = {
  id: string;
  status: string;
  createdAt: number;
  quantity: number;
};
export function CollectionTools({
  owner,
  collection,
  onChange,
}: {
  owner: MobileOwner;
  collection: Collection;
  onChange: (value: Collection) => void;
}) {
  const [text, setText] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{
      previewHash: string;
      rows: number;
      quantity: number;
      version: number;
    } | null>(null),
    [batches, setBatches] = useState<Batch[]>([]),
    [exported, setExported] = useState("");
  const reload = async () =>
    setBatches(await owner.request<Batch[]>({ op: "collection-batches" }));
  useEffect(() => {
    void reload().catch((e) => setError(message(e)));
  }, [owner, collection.version]);
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
  return (
    <section className="mobile-panel" aria-label="收藏交换与批次">
      <h2>收藏交换</h2>
      <p>
        使用与桌面端相同的 CSV，保留印刷
        ID、品相、数量、备注与愿望单。导入会增加持有数量，先预览再确认。
      </p>
      <div className="mobile-button-row">
        <button
          disabled={busy}
          onClick={() =>
            void act(async () => {
              const result = await file<{ text?: string; cancelled?: boolean }>(
                owner,
                { op: "pick-collection" },
              );
              if (!result.cancelled) {
                setText(result.text || "");
                setPreview(null);
              }
            })
          }
        >
          选择收藏 CSV
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void act(async () => {
              const result = await file<{ cancelled?: boolean }>(owner, {
                op: "share-collection",
              });
              setNotice(result.cancelled ? "已取消分享" : "已完成系统分享");
            })
          }
        >
          分享收藏 CSV
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void act(async () => {
              const result = await owner.request<{ text: string }>({
                op: "export-collection",
              });
              setExported(result.text);
            })
          }
        >
          显示导出文本
        </button>
      </div>
      {exported && (
        <label>
          收藏导出文本
          <textarea readOnly rows={5} value={exported} />
        </label>
      )}
      <label>
        收藏 CSV 内容
        <textarea
          rows={5}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPreview(null);
          }}
          placeholder="printingId,quantity,condition,notes,wishlist"
        />
      </label>
      <button
        disabled={busy || !text.trim()}
        onClick={() =>
          void act(async () => {
            const value = await owner.request<{
              previewHash: string;
              rows: number;
              quantity: number;
            }>({ op: "preview-collection", text });
            setPreview({ ...value, version: collection.version });
          })
        }
      >
        预览收藏导入
      </button>
      {preview && (
        <div className="mobile-import-preview">
          <p>
            将新增 {preview.quantity} 张卡牌，涉及 {preview.rows}{" "}
            条版次与品相记录。重复提交同一批次不会重复增加数量。
          </p>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const result = await owner.request<{
                  collection: Collection;
                  duplicate: boolean;
                }>({
                  op: "import-collection",
                  text,
                  previewHash: preview.previewHash,
                  expectedVersion: preview.version,
                });
                onChange(result.collection);
                setNotice(
                  result.duplicate
                    ? "这批资料已导入，未重复增加数量"
                    : "收藏已导入，可在下方撤销此批次",
                );
                setPreview(null);
                await reload();
              })
            }
          >
            确认增加到收藏
          </button>
        </div>
      )}
      <details>
        <summary>导入批次 · {batches.length}</summary>
        {batches.length ? (
          batches.map((batch) => (
            <article className="mobile-batch" key={batch.id}>
              <span>
                {new Date(batch.createdAt * 1000).toLocaleString()} ·{" "}
                {batch.quantity} 张 ·{" "}
                {batch.status === "undone" ? "已撤销" : "已导入"}
              </span>
              {batch.status === "applied" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = await owner.request<{
                        collection?: Collection;
                      }>({
                        op: "undo-collection",
                        batch: batch.id,
                        expectedVersion: collection.version,
                      });
                      if (result.collection) onChange(result.collection);
                      setNotice("已撤销导入数量；之后修改的备注和愿望单会保留");
                      await reload();
                    })
                  }
                >
                  撤销此批次
                </button>
              )}
            </article>
          ))
        ) : (
          <p>尚无导入记录。</p>
        )}
      </details>
      {error && (
        <p role="alert" className="mobile-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
export function CollectionEditor({
  owner,
  printingId,
  collection,
  onChange,
}: {
  owner: MobileOwner;
  printingId: string;
  collection: Collection;
  onChange: (value: Collection) => void;
}) {
  const [condition, setCondition] = useState<string>("未标注");
  const existing = (value: string): Holding =>
    collection.entries.find(
      (e) => e.printingId === printingId && e.condition === value,
    ) || {
      printingId,
      condition: value,
      quantity: 0,
      notes: "",
      wishlist: false,
    };
  const [row, setRow] = useState(() => existing("未标注")),
    [baseVersion, setBaseVersion] = useState(collection.version),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  const total = collection.entries
    .filter((e) => e.printingId === printingId)
    .reduce((n, e) => n + e.quantity, 0);
  return (
    <section className="mobile-panel" aria-label="收藏品相编辑">
      <h3>我的收藏 · 共 {total} 张</h3>
      <label>
        收藏品相
        <select
          aria-label="收藏品相"
          value={condition}
          onChange={(e) => {
            setCondition(e.target.value);
            setRow(existing(e.target.value));
            setBaseVersion(collection.version);
            setSaved(false);
            setError("");
          }}
        >
          {conditions.map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
      </label>
      <label>
        持有数量
        <input
          type="number"
          min={0}
          max={9999}
          step={1}
          value={row.quantity}
          onChange={(e) => {
            setRow({ ...row, quantity: Number(e.target.value) });
            setSaved(false);
          }}
        />
      </label>
      <label>
        收藏备注
        <textarea
          maxLength={500}
          rows={3}
          value={row.notes}
          onChange={(e) => {
            setRow({ ...row, notes: e.target.value });
            setSaved(false);
          }}
        />
      </label>
      <label className="mobile-check">
        <input
          type="checkbox"
          checked={row.wishlist}
          onChange={(e) => {
            setRow({ ...row, wishlist: e.target.checked });
            setSaved(false);
          }}
        />
        加入愿望单
      </label>
      <button
        className="primary"
        disabled={
          busy ||
          !Number.isInteger(row.quantity) ||
          row.quantity < 0 ||
          row.quantity > 9999
        }
        onClick={() => {
          setBusy(true);
          setError("");
          void owner
            .request<Collection>({
              op: "set-collection",
              changes: [row],
              expectedVersion: baseVersion,
            })
            .then((value) => {
              onChange(value);
              setBaseVersion(value.version);
              setSaved(true);
            })
            .catch((e) => setError(message(e)))
            .finally(() => setBusy(false));
        }}
      >
        保存此品相
      </button>
      <button
        disabled={busy}
        onClick={() => {
          setRow(existing(condition));
          setBaseVersion(collection.version);
          setSaved(false);
          setError("");
        }}
      >
        重新载入此品相
      </button>
      {error && (
        <p role="alert" className="mobile-error">
          {error}
        </p>
      )}
      {saved && <p role="status">此品相已保存</p>}
    </section>
  );
}
