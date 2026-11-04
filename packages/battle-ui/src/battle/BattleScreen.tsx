import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  Ellipsis,
  Maximize,
  Minimize,
  Palette,
  RefreshCw,
  Swords,
} from "lucide-react";
import { AppearanceSettings } from "./AppearanceSettings";
import {
  DEFAULT_APPEARANCE,
  appearanceStyle,
  type Appearance,
  type AppearanceStorage,
} from "./appearance";
import "./screen.css";
import "./mobile-arena.css";

/** A viewport of its own, independent of the library/practice navigation shell. */
export function BattleScreen({
  children,
  busy,
  onRefresh,
  appearanceStorage,
  mobile,
}: {
  children: ReactNode;
  busy: boolean;
  onRefresh: () => void;
  appearanceStorage?: AppearanceStorage;
  mobile?: {
    panelKey: string;
    summary: ReactNode;
    activity?: ReactNode;
    footer: ReactNode;
    menu: ReactNode;
    menuOpen: boolean;
    onMenuChange: (open: boolean) => void;
  };
}) {
  const screen = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDialogElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [displayError, setDisplayError] = useState("");
  const [appearance, setAppearance] = useState(
    () => appearanceStorage?.load() || DEFAULT_APPEARANCE,
  );
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [appearanceMessage, setAppearanceMessage] = useState("");
  const menuOpen = mobile?.menuOpen;
  const panelKey = mobile?.panelKey;
  useLayoutEffect(() => {
    if (body.current) body.current.scrollTop = 0;
  }, [panelKey]);
  useLayoutEffect(() => {
    const element = menu.current;
    if (menuOpen) element?.showModal();
    else element?.close();
  }, [menuOpen]);
  function saveAppearance(value: Appearance) {
    setAppearance(value);
    setSettingsOpen(false);
    try {
      appearanceStorage?.save(value);
      setAppearanceMessage("");
    } catch {
      setAppearanceMessage(
        "外观已应用到当前窗口，但浏览器未能保存设置；重新打开后可能恢复默认。请检查浏览器存储空间或隐私设置。",
      );
    }
  }

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
      className={`battle-page battle-screen${mobile ? " battle-screen-mobile" : ""}`}
      aria-label="对战与回放"
      style={appearanceStyle(appearance)}
    >
      {mobile ? (
        <header className="mobile-battle-header">
          {mobile.summary}
          <button
            className="secondary mobile-battle-menu-trigger"
            aria-haspopup="dialog"
            onClick={() => mobile.onMenuChange(true)}
          >
            <Ellipsis size={20} aria-hidden="true" /> 对局菜单
          </button>
        </header>
      ) : (
        <header className="battle-screen-header">
          <div className="battle-screen-brand">
            <Swords size={22} />
            <div>
              <span>PTCG LAB</span>
              <h1>对战平台</h1>
            </div>
          </div>
          <div className="battle-screen-actions">
            <button
              className="secondary"
              onClick={() => setSettingsOpen(true)}
              aria-haspopup="dialog"
            >
              <Palette size={15} />
              桌面外观
            </button>
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
      )}
      {displayError && (
        <p className="battle-display-message" role="status">
          {displayError}
        </p>
      )}
      {appearanceMessage && (
        <p className="battle-display-message" role="status">
          {appearanceMessage}
        </p>
      )}
      {mobile ? (
        <>
          {mobile.activity}
          <div className="mobile-battle-body" ref={body}>
            {children}
          </div>
          {mobile.footer}
          <dialog
            ref={menu}
            className="mobile-battle-menu"
            aria-label="对局菜单"
            onCancel={(event) => {
              event.preventDefault();
              mobile.onMenuChange(false);
            }}
          >
            <header>
              <h2>对局菜单</h2>
              <button
                className="secondary"
                onClick={() => mobile.onMenuChange(false)}
              >
                关闭菜单
              </button>
            </header>
            {mobile.menu}
            <div className="mobile-battle-menu-tools">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => {
                  mobile.onMenuChange(false);
                  onRefresh();
                }}
              >
                <RefreshCw size={16} aria-hidden="true" /> 刷新局面
              </button>
              <button
                className="secondary"
                onClick={() => {
                  mobile.onMenuChange(false);
                  setSettingsOpen(true);
                }}
                aria-haspopup="dialog"
              >
                <Palette size={16} aria-hidden="true" /> 牌桌外观
              </button>
            </div>
          </dialog>
        </>
      ) : (
        children
      )}
      {settingsOpen && (
        <AppearanceSettings
          value={appearance}
          onApply={saveAppearance}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </main>,
    document.body,
  );
}
