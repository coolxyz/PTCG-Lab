import { useEffect, useState } from "react";
import { message, type MobileOwner } from "./contracts";
import { file } from "./CollectionTools";
type Preview = {
  package: string;
  base: string;
  catalogVersion: string;
  cards: number;
  added: number;
  removed: number;
  engineMappingsChanged: number;
  referencedMissing: string[];
  canApply: boolean;
  stale?: boolean;
};
export function ResourceSettings({
  owner,
  blocked,
  onApplied,
}: {
  owner: MobileOwner;
  blocked: boolean;
  onApplied: () => Promise<void>;
}) {
  const [url, setURL] = useState(""),
    [sha, setSHA] = useState(""),
    [preview, setPreview] = useState<Preview | null>(null),
    [current, setCurrent] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [confirm, setConfirm] = useState(false);
  const status = async () => {
    const result = await owner.request<{
      catalogVersion: string;
      pending: Preview | null;
    }>({ op: "catalog-status" });
    setCurrent(result.catalogVersion);
    setPreview(result.pending);
    setConfirm(false);
  };
  useEffect(() => {
    void status().catch((e) => setError(message(e)));
  }, [owner]);
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
  const valid = /^[0-9a-f]{64}$/.test(sha.trim());
  return (
    <section className="mobile-panel" aria-label="展示资料更新">
      <h2>展示资料更新</h2>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            const result = await owner.request<{ removed: number }>({
              op: "prune-catalog-updates",
            });
            setNotice(
              `已清理 ${result.removed} 份未使用展示资料；当前目录、待安装包和用户数据均保留`,
            );
          })
        }
      >
        清理未使用展示资料
      </button>
      <p>
        单独更新卡表、中文资料与图片索引，无需下载所有卡图。仅接受发布者提供的移动展示
        JSON 和 SHA-256；不会安装规则或更改对战身份。
      </p>
      {blocked && <p role="status">请先保存卡组草稿，再应用资料更新。</p>}
      <label>
        资料文件 SHA-256
        <input
          autoCapitalize="none"
          autoCorrect="off"
          value={sha}
          onChange={(e) => {
            setSHA(e.target.value.toLowerCase());
            setPreview(null);
            setConfirm(false);
          }}
          maxLength={64}
        />
      </label>
      <label>
        资料下载地址
        <input
          type="url"
          autoCapitalize="none"
          autoCorrect="off"
          placeholder="https://assets.example.com/mobile-display.json"
          value={url}
          onChange={(e) => {
            setURL(e.target.value);
            setPreview(null);
            setConfirm(false);
          }}
        />
      </label>
      <div className="mobile-button-row">
        <button
          disabled={busy || !valid || !url.trim()}
          onClick={() =>
            void act(async () => {
              const value = await file<Preview>(owner, {
                op: "download-catalog",
                url: url.trim(),
                sha256: sha.trim(),
              });
              setPreview(value);
              setConfirm(false);
            })
          }
        >
          下载并校验资料
        </button>
        <button
          disabled={busy || !valid}
          onClick={() =>
            void act(async () => {
              const value = await file<Preview & { cancelled?: boolean }>(
                owner,
                { op: "pick-catalog", sha256: sha.trim() },
              );
              if (!value.cancelled) {
                setPreview(value);
                setConfirm(false);
              }
            })
          }
        >
          选择资料 JSON 文件
        </button>
      </div>
      {preview && (
        <div className="mobile-import-preview">
          <h3>更新预览</h3>
          <p>
            共 {preview.cards.toLocaleString()} 张 · 新增 {preview.added} · 移除{" "}
            {preview.removed} · 效果映射变化 {preview.engineMappingsChanged}
          </p>
          <details>
            <summary>当前与候选目录</summary>
            <p className="mobile-version">
              当前：{current}
              <br />
              候选：{preview.catalogVersion}
              <br />
              文件 SHA-256：{preview.package}
            </p>
          </details>
          {!preview.canApply && (
            <p role="alert">
              更新缺少收藏或已保存卡组引用的 ID，不能应用：
              {preview.referencedMissing.slice(0, 10).join("、")}
            </p>
          )}
          {preview.stale && (
            <p role="alert">当前目录已变化，请重新选择或下载后预览。</p>
          )}
          <label className="mobile-check">
            <input
              type="checkbox"
              checked={confirm}
              onChange={(e) => setConfirm(e.target.checked)}
            />
            我已核对资料来源、校验值及变更范围
          </label>
          <button
            className="primary"
            disabled={
              busy || blocked || !confirm || !preview.canApply || preview.stale
            }
            onClick={() =>
              void act(async () => {
                const applied = await owner.request<{ catalogVersion: string }>(
                  {
                    op: "apply-catalog",
                    package: preview.package,
                    base: preview.base,
                  },
                );
                setPreview(null);
                setConfirm(false);
                setCurrent(applied.catalogVersion);
                setNotice("展示资料已更新；收藏、卡组和对战记录均保留");
                await onApplied();
                await status();
              })
            }
          >
            确认应用目录更新
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="mobile-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
