import { useLayoutEffect, useRef, useState, type ImgHTMLAttributes } from "react";
import { artworkRetryDelay, type ArtworkStatus } from "./artwork-retry";

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "onError" | "onLoad"> & {
  src?: string;
  onStatusChange?: (status: ArtworkStatus) => void;
};

/** Native lazy loading and immutable URLs; only failed local artwork is retried. */
export function RetryImage({ src, onStatusChange, style, ...props }: Props) {
  const [broken, setBroken] = useState(false);
  const [generation, setGeneration] = useState(0);
  const callback = useRef(onStatusChange);
  callback.current = onStatusChange;
  const failed = useRef<() => void>(() => {});
  const loaded = useRef<() => void>(() => {});

  useLayoutEffect(() => {
    let alive = true, attempts = 0, probing = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const notify = (state: ArtworkStatus["state"], message: string) => {
      if (alive) callback.current?.({ state, message });
    };
    setBroken(false);
    setGeneration(0);
    notify("loading", "正在加载卡图");
    const eligible = !!src && (/^\/api\/sync\/(?:artwork|images)\//.test(src) || /^mobile-artwork:\/\/localhost\/[a-f0-9]{40}\//.test(src));

    async function probe() {
      if (!alive || probing || !src) return;
      probing = true;
      controller = new AbortController();
      timeout = setTimeout(() => controller?.abort(), 30000);
      let response: Response | undefined, data: any;
      try {
        // Failed API responses are no-store. A successful probe warms the normal
        // browser cache; the remounted img uses the exact same immutable URL.
        response = await fetch(src, { signal: controller.signal });
        if (response.ok) {
          await response.arrayBuffer();
          attempts++;
          if (alive) setGeneration(value => value + 1);
          return;
        }
        data = await response.json().catch(() => undefined);
      } catch {
        data = { message: "本机卡图请求暂时失败" };
      } finally {
        probing = false;
        clearTimeout(timeout);
      }
      if (!alive) return;
      const delay = artworkRetryDelay(attempts, response, data);
      if (delay === null) {
        notify("failed", data?.message || "卡图暂不可用，可在更新页面手动重试");
        return;
      }
      notify("retrying", `卡图暂不可用，${Math.ceil(delay / 1000)} 秒后自动重试（${attempts + 1}/3）`);
      timer = setTimeout(() => { timer = undefined; attempts++; void probe(); }, delay);
    }
    failed.current = () => {
      if (!alive) return;
      setBroken(true);
      if (!eligible) { notify("failed", "卡图暂不可用"); return; }
      // Prevent repeated decode errors of a 200 response from looping forever.
      if (attempts >= 3) { notify("failed", "卡图仍不可用，可在更新页面手动重试"); return; }
      if (timer) return;
      void probe();
    };
    loaded.current = () => {
      if (!alive) return;
      clearTimeout(timer); timer = undefined;
      setBroken(false); notify("ready", "");
    };
    return () => {
      alive = false;
      clearTimeout(timer); clearTimeout(timeout); controller?.abort();
    };
  }, [src]);

  return <img {...props} key={generation} src={src} loading={generation ? "eager" : props.loading} style={{ ...style, ...(broken ? { display: "none" } : {}) }}
    data-artwork-state={broken ? "unavailable" : "loading"}
    onError={() => failed.current()} onLoad={() => loaded.current()} />;
}
