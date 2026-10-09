import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";
import { X } from "lucide-react";

export function DetailImage({ card, onError, compact = false }: { card: { cnName: string; image: { url: string | null; label: string; source?: string } }; onError: (event: SyntheticEvent<HTMLImageElement>) => void; compact?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const preview = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const dialog = preview.current;
    if (expanded) dialog?.showModal();
    return () => {
      dialog?.close();
      if (expanded) trigger.current?.focus({ preventScroll: true });
    };
  }, [expanded]);
  const label = card.cnName + " · " + card.image.label;
  return (
    <div className={compact ? "variant-image" : "real-image"}>
      <button
        ref={trigger}
        className="image-zoom-trigger"
        aria-label={"放大卡图：" + card.cnName}
        aria-haspopup="dialog"
        onClick={() => setExpanded(true)}
      >
        <img src={card.image.url!} alt={label} onError={onError} />
        <span>点击放大卡图</span>
      </button>
      {!compact && <small>{card.image.label} · 仅替代展示，身份仍为简中印刷</small>}
      {!compact && card.image.source && <a href={card.image.source} target="_blank" rel="noreferrer">查看卡图来源</a>}
      {expanded && (
        <dialog
          ref={preview}
          className="image-preview"
          aria-label={"放大卡图：" + card.cnName}
          onCancel={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setExpanded(false);
          }}
          onClick={(event) => {
            if (event.target === event.currentTarget) setExpanded(false);
          }}
        >
          <button
            className="image-preview-close"
            aria-label="关闭放大卡图"
            onClick={() => setExpanded(false)}
            autoFocus
          >
            <X size={24} />
          </button>
          <img src={card.image.url!} alt={label} onError={onError} />
        </dialog>
      )}
    </div>
  );
}
