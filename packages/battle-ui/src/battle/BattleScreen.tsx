import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Maximize, Minimize, RefreshCw, Swords } from "lucide-react";
import "./screen.css";

/** A viewport of its own, independent of the library/practice navigation shell. */
export function BattleScreen({
  children,
  busy,
  onRefresh,
}: {
  children: ReactNode;
  busy: boolean;
  onRefresh: () => void;
}) {
  const screen = useRef<HTMLElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [displayError, setDisplayError] = useState("");

  useLayoutEffect(() => {
    const focused = document.activeElement as HTMLElement | null;
    document.body.classList.add("battle-screen-open");
    screen.current?.focus({ preventScroll: true });
    return () => {
      document.body.classList.remove("battle-screen-open");
      if (focused?.isConnected) focused.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    const element = screen.current;
    const update = () => setFullscreen(document.fullscreenElement === element);
    document.addEventListener("fullscreenchange", update);
    return () => {
      document.removeEventListener("fullscreenchange", update);
      if (document.fullscreenElement === element)
        void document.exitFullscreen().catch(() => {});
    };
  }, []);

  async function toggleFullscreen() {
    setDisplayError("");
    try {
      if (document.fullscreenElement === screen.current)
        await document.exitFullscreen();
      else if (document.fullscreenEnabled && screen.current?.requestFullscreen)
        await screen.current.requestFullscreen();
      else setDisplayError("当前浏览器不支持系统全屏，已使用完整窗口显示。");
    } catch {
      setDisplayError("暂时无法进入系统全屏，仍可在完整窗口中对战。");
    }
  }

  return createPortal(
    <main
      ref={screen}
      tabIndex={-1}
      className="battle-page battle-screen"
      aria-label="对战与回放"
    >
      <header className="battle-screen-header">
        <div className="battle-screen-brand">
          <Swords size={22} />
          <div>
            <span>PTCG LAB</span>
            <h1>对战平台</h1>
          </div>
        </div>
        <div className="battle-screen-actions">
          <button className="secondary" disabled={busy} onClick={onRefresh}>
            <RefreshCw size={15} />
            刷新局面
          </button>
          <button
            className="secondary"
            onClick={toggleFullscreen}
            aria-pressed={fullscreen}
          >
            {fullscreen ? <Minimize size={15} /> : <Maximize size={15} />}
            {fullscreen ? "退出全屏" : "全屏显示"}
          </button>
        </div>
      </header>
      {displayError && (
        <p className="battle-display-message" role="status">
          {displayError}
        </p>
      )}
      {children}
    </main>,
    document.body,
  );
}
