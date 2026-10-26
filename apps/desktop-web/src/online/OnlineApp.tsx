import { useEffect, useState, useSyncExternalStore } from "react";
import { BattleArena } from "@ptcg/battle-ui";
import { snapshotView, replayView } from "../presentation";
import { OnlineController, errorText } from "./controller";
import { connect, authorizeProduct } from "./transport";
import { onlineStorage } from "./storage";
import "./online.css";
import { OnlineLobby } from "./OnlineLobby";
import { visibleHistory, replayTimeline } from "./history";
import { browserAppearanceStorage } from "../appearance-storage";

export function OnlineApp({ product = false }: { product?: boolean }) {
  const [code, setCode] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [controller, setController] = useState<OnlineController | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let current: OnlineController | undefined;
    let ended = false;
    void connect(onlineStorage.windowId())
      .then((api) => {
        if (ended) {
          void api.close();
          return;
        }
        current = new OnlineController(api, onlineStorage);
        setController(current);
        void current.start();
      })
      .catch((e) => setError(errorText(e)));
    const leave = () => current?.dispose();
    window.addEventListener("pagehide", leave);
    return () => {
      ended = true;
      leave();
      window.removeEventListener("pagehide", leave);
    };
  }, [attempt]);
  if (!controller)
    return (
      <main className="online-lobby">
        <h1>网络对战</h1>
        <p role={error ? "alert" : "status"}>{error || "正在连接本地后台…"}</p>
        {error &&
          (product ? (
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
      </main>
    );
  return <Connected controller={controller} product={product} />;
}
function Connected({
  controller: c,
  product,
}: {
  controller: OnlineController;
  product: boolean;
}) {
  const s = useSyncExternalStore(c.subscribe, c.snapshot);
  if (s.showing && s.view)
    return (
      <>
        <BattleArena
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
                  {s.nextCursor ? " · 正在继续加载…" : " · 回放已完整加载"}
                </span>
              )}
              {s.replay && s.nextCursor && (
                <button disabled={s.busy} onClick={c.more}>
                  加载更多回放
                </button>
              )}
            </div>
          }
          onSubmit={s.lease && !s.replay && !s.pending ? c.submit : undefined}
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
              {!s.view.done && (
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
    />
  );
}
