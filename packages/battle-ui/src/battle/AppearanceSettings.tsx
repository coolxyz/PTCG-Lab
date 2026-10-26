import { useLayoutEffect, useRef, useState } from "react";
import {
  appearanceStyle,
  DEFAULT_APPEARANCE,
  importPlaymat,
  PLAYMATS,
  type Appearance,
} from "./appearance";
import "./appearance.css";

export function AppearanceSettings({
  value,
  onApply,
  onClose,
}: {
  value: Appearance;
  onApply: (value: Appearance) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const [draft, setDraft] = useState(value);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useLayoutEffect(() => {
    alive.current = true;
    const focused = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    return () => {
      alive.current = false;
      element?.close();
      if (focused?.isConnected) focused.focus({ preventScroll: true });
    };
  }, []);

  async function upload(file?: File) {
    if (!file) return;
    setLoading(true);
    setError("");
    try {
      const customImage = await importPlaymat(file);
      if (alive.current) setDraft({ preset: "custom", customImage });
    } catch (reason) {
      if (alive.current)
        setError(reason instanceof Error ? reason.message : "图片导入失败。");
    } finally {
      if (alive.current) setLoading(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className="battle-appearance"
      aria-labelledby="battle-appearance-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <h2 id="battle-appearance-title">桌面外观</h2>
          <p>人机练习、网络对战与回放共用。</p>
        </div>
        <button
          type="button"
          className="secondary"
          onClick={onClose}
          aria-label="关闭桌面外观"
        >
          关闭
        </button>
      </header>
      <div
        className="appearance-preview"
        style={appearanceStyle(draft)}
        aria-label="桌面效果预览"
      >
        <span className="appearance-preview-title">桌面效果预览</span>
        <div className="appearance-preview-cards" aria-hidden="true">
          <span>普通牌</span>
          <span className="usable">可操作</span>
          <span className="chosen">✓ 已选择</span>
        </div>
      </div>
      <fieldset disabled={loading} className="appearance-options">
        <legend>纯色桌面</legend>
        {PLAYMATS.filter((mat) => !("image" in mat)).map((mat) => (
          <label key={mat.id}>
            <input
              type="radio"
              name="battle-playmat"
              value={mat.id}
              checked={draft.preset === mat.id}
              onChange={() => setDraft({ ...draft, preset: mat.id })}
            />
            <span
              className="appearance-swatch"
              style={appearanceStyle({ preset: mat.id })}
            />
            <span>{mat.name}</span>
          </label>
        ))}
      </fieldset>
      <fieldset
        disabled={loading}
        className="appearance-options appearance-mats"
      >
        <legend>赛事风格桌垫</legend>
        {PLAYMATS.filter((mat) => "image" in mat).map((mat) => (
          <label key={mat.id}>
            <input
              type="radio"
              name="battle-playmat"
              value={mat.id}
              checked={draft.preset === mat.id}
              onChange={() => setDraft({ ...draft, preset: mat.id })}
            />
            <span
              className="appearance-swatch"
              style={appearanceStyle({ preset: mat.id })}
            />
            <span>{mat.name}</span>
          </label>
        ))}
      </fieldset>
      <p className="appearance-note">
        内置桌垫为原创赛场图案，非官方赛事图片。也可以导入你已有的 PTCG 桌垫。
      </p>
      <fieldset disabled={loading} className="appearance-upload">
        <legend>自己的桌垫</legend>
        <label>
          导入桌垫图片
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(event) => {
              void upload(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
        {draft.customImage && (
          <div className="appearance-custom-actions">
            <label>
              <input
                type="radio"
                name="battle-playmat"
                checked={draft.preset === "custom"}
                onChange={() => setDraft({ ...draft, preset: "custom" })}
              />
              使用已导入图片
            </label>
            <button
              type="button"
              className="secondary"
              onClick={() =>
                setDraft({
                  preset:
                    draft.preset === "custom"
                      ? DEFAULT_APPEARANCE.preset
                      : draft.preset,
                })
              }
            >
              移除图片
            </button>
          </div>
        )}
        <p className="appearance-note">
          支持 JPG / PNG / WebP，最大 5
          MB。图片仅保存在当前浏览器，不上传服务器；自动裁切铺满桌面并加暗色遮罩。
        </p>
      </fieldset>
      {loading && <p role="status">正在处理图片…</p>}
      {error && (
        <p role="alert" className="appearance-error">
          {error}
        </p>
      )}
      <footer>
        <button
          type="button"
          className="secondary"
          disabled={loading}
          onClick={() => setDraft(DEFAULT_APPEARANCE)}
        >
          恢复默认
        </button>
        <div>
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="primary"
            disabled={loading}
            onClick={() => onApply(draft)}
          >
            保存外观
          </button>
        </div>
      </footer>
    </dialog>
  );
}
