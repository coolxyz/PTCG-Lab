import { useEffect, useState, useSyncExternalStore } from "react";
import { BattleArena } from "@ptcg/battle-ui";
import { snapshotView, replayView } from "../presentation";
import { OnlineController, errorText } from "./controller";
import { connect, authorizeProduct, type OnlineAPI } from "./transport";
import { onlineStorage, type OnlineStorage } from "./storage";
import "./online.css";
import { OnlineLobby } from "./OnlineLobby";
import { visibleHistory, replayTimeline } from "./history";
import { browserAppearanceStorage } from "../appearance-storage";

export function OnlineApp({
  product = false,
  connectAPI = connect,
  storage = onlineStorage,
  native = false,
}: {
  product?: boolean;
  connectAPI?: (windowId: string) => Promise<OnlineAPI>;
  storage?: OnlineStorage;
  native?: boolean;
}) {
  const [code, setCode] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [controller, setController] = useState<OnlineController | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let current: OnlineController | undefined;
    let ended = false;
    void connectAPI(storage.windowId())
      .then((api) => {
        if (ended) {
          void api.close();
          return;
        }
        current = new OnlineController(api, storage);
        setController(current);
        void current.start();
      })
      .catch((e) => setError(errorText(e)));
    const leave = (event: PageTransitionEvent) =>
      native && event.persisted ? current?.suspend() : current?.dispose();
    const visibility = () => {
      if (native) {
        if (document.visibilityState === "hidden") current?.suspend();
        else void current?.resume();
      }
    };
    const show = (event: PageTransitionEvent) => {
      if (native && event.persisted) void current?.resume();
    };
    window.addEventListener("pagehide", leave);
    if (native) {
      document.addEventListener("visibilitychange", visibility);
      window.addEventListener("pageshow", show);
    }
    return () => {
      ended = true;
      current?.dispose();
      window.removeEventListener("pagehide", leave);
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pageshow", show);
    };
  }, [attempt, connectAPI, storage, native]);
  if (!controller)
    return (
      <section className="workspace-page online-lobby">
        <header className="page-heading">
          <div>
            <div className="eyebrow">PLAY TOGETHER</div>
            <h1>网络对战</h1>
            <p>使用平台卡组，邀请朋友开始对战。</p>
          </div>
        </header>
        <p role={error ? "alert" : "status"}>
          {error || (native ? "正在读取对战资料…" : "正在连接本地后台…")}
        </p>
        {error &&
          (native ? (
            <div>
              <p>
                请检查服务器设置和网络连接；已保存的身份和待确认操作会保留。
              </p>
              <button
                onClick={() => {
                  setError("");
                  setAttempt((n) => n + 1);
                }}
              >
                重试连接
              </button>
            </div>
          ) : product ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await authorizeProduct(code);
                  setCode("");
                  setError("");
                  setAttempt((n) => n + 1);
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              <p>
                首次使用请填入 APP
                启动终端显示的本机授权码。授权过期时在终端输入 r
                获取新码；无需打开 HTML 文件。
              </p>
              <label>
                本机授权码
                <input
                  aria-label="本机授权码"
                  type="password"
                  autoComplete="off"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </label>
              <button disabled={!code.trim()}>授权本机浏览器</button>
            </form>
          ) : (
            <p>请使用本地启动器打开窗口。刷新不会创建新的游客身份。</p>
          ))}
      </section>
    );
  return (
    <Connected controller={controller} product={product} native={native} />
  );
}
function Connected({
  controller: c,
  product,
  native,
}: {
  controller: OnlineController;
  product: boolean;
  native: boolean;
}) {
  const s = useSyncExternalStore(c.subscribe, c.snapshot);
  if (s.showing && s.view)
    return (
      <>
        <BattleArena
          mobile={native}
          appearanceStorage={browserAppearanceStorage}
          view={{
            ...snapshotView(s.view),
            events: visibleHistory(s.history, s.cards),
          }}
          cards={s.cards}
          viewerSeat={s.view.viewerSeat}
          opponentLabel="对手"
          busy={s.busy}
          error={s.error}
          pendingCommand={s.pending}
          statusLabel={
            <div className="online-controls">
              <span>
                {s.view.viewerSeat === "PLAYER2" ? "玩家 2" : "玩家 1"} ·{" "}
                {s.replay ? "只读回放" : s.lease ? "本窗口可操作" : "只读观察"}
                {s.missing ? " · 缺少本地图源，使用文字显示" : ""}
              </span>
              {s.replay && (
                <span role="status">
                  已加载 {s.replay.frames.length} 步
                  {s.replayNotice
                    ? ` · ${s.replayNotice}`
                    : s.nextCursor
                      ? " · 正在继续加载…"
                      : " · 回放已完整加载"}
                </span>
              )}
              {s.replay && s.nextCursor && (
                <button disabled={s.busy || !!s.replayNotice} onClick={c.more}>
                  加载更多回放
                </button>
              )}
            </div>
          }
          onSubmit={s.lease && !s.replay && !s.pending ? c.submit : undefined}
          onAcquireControl={
            native && !s.lease && !s.pending && !s.replay
              ? c.control
              : undefined
          }
          onRetry={s.pending ? c.recover : c.refresh}
          onRefresh={c.refresh}
          onLeave={c.leave}
          onResign={s.lease && !s.pending && !s.replay ? c.resign : undefined}
          onOpenReplay={c.replay}
          modeControls={
            <div className="online-controls">
              <small>记录范围：你可见的局面变化。</small>
              <details>
                <summary>对局编号</summary>
                <code>{s.view.matchId}</code>
              </details>
              {!native && !s.view.done && (
                <button
                  disabled={s.busy || s.pending || !!s.replay}
                  onClick={c.control}
                >
                  取得操作权
                </button>
              )}
              {s.pending && (
                <>
                  <button disabled={s.busy} onClick={c.recover}>
                    恢复待确认操作
                  </button>
                  <button disabled={s.busy} onClick={c.resolve}>
                    核对已拒绝的操作
                  </button>
                </>
              )}
            </div>
          }
          replay={
            s.replay
              ? {
                  frame: {
                    ...replayView(s.replay, s.frame),
                    events: visibleHistory(
                      s.replay.frames.slice(0, s.frame + 1),
                      s.cards,
                    ),
                  },
                  seq: s.frame,
                  end: s.replay.frames.length - 1,
                  timeline: replayTimeline(s.replay),
                  playing: s.playing,
                  speed: s.speed,
                  animate: true,
                  onSeek: c.seek,
                  onPlayingChange: c.play,
                  onSpeedChange: c.speed,
                  onClose: c.closeReplay,
                }
              : undefined
          }
        />
      </>
    );
  return (
    <OnlineLobby
      key={`${s.profile}:${s.status?.identity?.userId || ""}`}
      controller={c}
      state={s}
      product={product}
      native={native}
    />
  );
}
