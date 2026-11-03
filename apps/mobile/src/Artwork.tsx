import { useEffect, useRef, useState } from "react";
import type { MobileOwner, MobileError } from "./contracts";
import { message } from "./contracts";

/** Only visible cards request native I/O; no URL, token or filesystem access. */
export function Artwork({
  owner,
  id,
  name,
  enabled = true,
  interactive = true,
}: {
  owner: MobileOwner;
  id: string;
  name: string;
  enabled?: boolean;
  interactive?: boolean;
}) {
  const host = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false),
    [url, setUrl] = useState(""),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!host.current || !enabled) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "120px" },
    );
    observer.observe(host.current);
    return () => observer.disconnect();
  }, [enabled, id]);
  useEffect(() => {
    if (!visible || !enabled) return;
    let active = true,
      objectUrl = "",
      timer: ReturnType<typeof setTimeout> | undefined,
      attempt = 0;
    setUrl("");
    setError("");
    const load = async () => {
      try {
        const data = await owner.artwork(id);
        if (!active) return;
        objectUrl = URL.createObjectURL(
          new Blob([new Uint8Array(data.bytes)], { type: data.mime }),
        );
        setUrl(objectUrl);
        setError("");
      } catch (error) {
        if (!active) return;
        const delay = (error as MobileError)?.retryAfterSeconds;
        if (
          typeof delay === "number" &&
          Number.isFinite(delay) &&
          delay >= 0 &&
          attempt < 3
        ) {
          attempt++;
          setError(`卡图暂不可用，稍后自动重试（${attempt}/3）`);
          timer = setTimeout(
            () => void load(),
            Math.max(delay, [2, 5, 15][attempt - 1]) * 1000,
          );
        } else setError(message(error));
      }
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [owner, id, visible, enabled, retry]);
  return (
    <figure className="mobile-artwork" ref={host}>
      {url ? (
        <img
          src={url}
          alt={name}
          onError={() => {
            setUrl("");
            setError("图片无法显示，请重试");
          }}
        />
      ) : (
        <div className="mobile-card-placeholder" aria-label={`${name}卡图占位`}>
          <span className="master-ball">M</span>
          <small>{enabled ? error || "等待卡图" : "暂无已确认卡图"}</small>
        </div>
      )}
      {error && interactive && (
        <button
          className="image-retry"
          onClick={(event) => {
            event.stopPropagation();
            setRetry((n) => n + 1);
          }}
        >
          重试卡图
        </button>
      )}
    </figure>
  );
}
