import React, { useEffect, useState } from "react";

export function CardArtwork({ id, uploaded, onChange }: { id: string; uploaded?: boolean; onChange: (card: any) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!file) { setPreview(""); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  async function save(remove = false) {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/cards/${encodeURIComponent(id)}/image`, {
        method: remove ? "DELETE" : "PUT",
        headers: remove ? undefined : { "Content-Type": file!.type || "application/octet-stream" },
        body: remove ? undefined : file,
      });
      const card = await response.json();
      if (!response.ok) throw new Error(card.message || "卡图保存失败");
      onChange(card); setFile(null);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="artwork-upload" aria-label="自定义卡图">
    <h3>补充卡图</h3>
    <p className="muted">请选择与当前卡号对应的图片。JPG、PNG、WebP，最大 8 MB；仅保存在本机。</p>
    <input aria-label="选择卡图文件" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={e => {
      const selected = e.target.files?.[0]; setError("");
      if (selected && selected.size > 8 * 1024 * 1024) { setError("卡图不能超过 8 MB"); setFile(null); }
      else setFile(selected || null);
      e.target.value = "";
    }} />
    {preview && <img className="artwork-preview" src={preview} alt="待上传卡图预览" />}
    {error && <p role="alert">{error}</p>}
    <div className="actions">
      {file && <button disabled={busy} onClick={() => save()}>{busy ? "保存中…" : "保存卡图"}</button>}
      {uploaded && <button className="secondary" disabled={busy} onClick={() => save(true)}>恢复默认卡图</button>}
    </div>
  </section>;
}

export type ChineseDetails = {
  source?: string; revision?: number; textStatus?: string;
  sections: { kind: string; name: string; text: string; damage?: string; cost?: string[]; locale?: string; versionLabel?: string; missingText?: boolean; source?: string }[];
};

export function CardDescription({ details, types }: { details?: ChineseDetails; types: Record<string, string> }) {
  return <section className="chinese-description" aria-label="中文卡牌说明">
    <h3>中文卡牌说明</h3>
    {!details?.sections?.length && <p className="muted">来源尚未提供可提取的中文效果说明。</p>}
    {details?.sections?.map((s, i) => <div className="card-rule-section" key={i}>
      <strong>{s.name === s.kind ? s.kind : `${s.kind} · ${s.name}`}{s.damage && `　${s.damage}`}</strong>
      {!!s.cost?.length && <small>所需能量：{s.cost.map(t => types[t] || t).join("、")}</small>}
      {s.versionLabel && <small>{s.versionLabel}</small>}
      {s.text && <p>{s.text}</p>}
      {s.missingText && <p className="muted">此效果的中文说明待补。</p>}
      {s.locale === "zh-Hant" && <small>来源提供繁体中文说明</small>}
      {s.source && <a href={s.source} target="_blank" rel="noreferrer">查看补充说明来源</a>}
    </div>)}
    {details?.source && <a href={details.source} target="_blank" rel="noreferrer">查看百科原文{details.revision ? `（版本 ${details.revision}）` : ""}</a>}
  </section>;
}
