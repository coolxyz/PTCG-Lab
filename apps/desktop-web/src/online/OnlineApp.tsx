import { useEffect, useState, useSyncExternalStore } from "react";
import { BattleArena } from "@ptcg/battle-ui";
import { snapshotView, replayView } from "../presentation";
import { OnlineController, errorText } from "./controller";
import { connect } from "./transport";
import { onlineStorage } from "./storage";
import "./online.css";

export function OnlineApp() {
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
  }, []);
  if (!controller)
    return (
      <main className="online-lobby">
        <h1>网络对战</h1>
        <p role={error ? "alert" : "status"}>{error || "正在连接本地后台…"}</p>
        {error && <p>请使用本地启动器打开窗口。刷新不会创建新的游客身份。</p>}
      </main>
    );
  return <Connected controller={controller} />;
}
function Connected({ controller: c }: { controller: OnlineController }) {
  const s = useSyncExternalStore(c.subscribe, c.snapshot);
  const [name, setName] = useState("训练家"),
    [deck, setDeck] = useState(""),
    [invite, setInvite] = useState(""),
    [match, setMatch] = useState("");
  const locked =
    s.busy || !!s.remembered.job || !!s.status?.reauthenticationRequired;
  if (s.showing && s.view)
    return (
      <>
        <BattleArena
          view={snapshotView(s.view)}
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
                  frame: replayView(s.replay, s.frame),
                  seq: s.frame,
                  end: s.replay.frames.length - 1,
                  timeline: [],
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
    <main className="online-lobby">
      <header>
        <div>
          <p className="online-eyebrow">PTCG LAB / ONLINE</p>
          <h1>网络对战</h1>
          <p>邀请一位朋友，通过本地窗口开始对战。</p>
        </div>
        <a href="/" target="_blank" rel="noopener noreferrer">
          打开另一个窗口
        </a>
      </header>
      {s.error && <p role="alert">{s.error}</p>}
      <section>
        <h2>连接与身份</h2>
        <label>
          对战服务器
          <select
            aria-label="对战服务器"
            value={s.profile}
            disabled={s.busy}
            onChange={(e) => void c.select(e.target.value)}
          >
            {s.profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id} · {p.origin}
              </option>
            ))}
          </select>
        </label>
        <p role="status">
          {s.status?.identity ? "游客身份已连接" : "尚未创建游客身份"}
          {s.status?.reauthenticationRequired ? " · 需要重新认证" : ""}
        </p>
        {!s.status?.identity && (
          <>
            <label>
              玩家名称
              <input
                aria-label="玩家名称"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <button
              disabled={s.busy || !s.connected}
              onClick={() => void c.guest(name)}
            >
              创建游客身份
            </button>
          </>
        )}
        <button disabled={s.busy} onClick={c.refresh}>
          刷新连接状态
        </button>
        <p className="online-muted">
          身份保存在本机后台。不要清理凭据目录，否则游客对局可能无法找回。
        </p>
      </section>
      {s.status?.identity && (
        <>
          <section>
            <h2>选择卡组</h2>
            <label>
              规则版本
              <select
                aria-label="规则版本"
                value={s.rule}
                disabled={locked || !!s.room}
                onChange={(e) => c.rule(e.target.value)}
              >
                {s.caps?.ruleReleaseIds.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
            <label>
              卡组清单
              <textarea
                aria-label="卡组清单"
                rows={5}
                value={deck}
                disabled={locked || !!s.room}
                onChange={(e) => setDeck(e.target.value)}
                placeholder={'[{"printingId":"卡牌印刷编号","quantity":4}]'}
              />
            </label>
            <p className="online-muted">
              粘贴 printingId 与 quantity 的 JSON
              数组，仅提交卡组清单，不上传收藏或图片。
            </p>
            <button
              disabled={locked || !s.rule || !!s.room}
              onClick={() => void c.deck(deck)}
            >
              校验并提交卡组
            </button>
            {s.remembered.revision && <p role="status">卡组版本已就绪</p>}
          </section>
          {s.remembered.job && (
            <section role="status">
              <p>上次大厅操作尚未确认，请重试原请求。</p>
              <button disabled={s.busy} onClick={c.retryLobby}>
                重试大厅操作
              </button>
            </section>
          )}
          <section>
            <h2>房间邀请</h2>
            {!s.room ? (
              <>
                <button
                  disabled={
                    locked ||
                    !s.remembered.revision ||
                    !s.caps?.acceptingMatches
                  }
                  onClick={c.create}
                >
                  创建邀请房间
                </button>
                <label>
                  房间码
                  <input
                    aria-label="房间码"
                    value={invite}
                    onChange={(e) => setInvite(e.target.value)}
                    maxLength={10}
                  />
                </label>
                <button
                  disabled={
                    locked ||
                    !s.remembered.revision ||
                    invite.trim().length !== 10
                  }
                  onClick={() => void c.join(invite)}
                >
                  加入房间
                </button>
              </>
            ) : (
              <>
                <p>
                  房间码{" "}
                  <strong data-testid="invite-code">{s.room.inviteCode}</strong>
                </p>
                <p>
                  状态：
                  {(
                    {
                      waiting: "等待双方准备",
                      initializing: "正在初始化对局",
                      initialized: "对局已创建",
                      closed: "房间已关闭",
                      technical_abort: "初始化失败",
                    } as Record<string, string>
                  )[s.room.status] || s.room.status}
                </p>
                <ul>
                  {s.room.members.map((m) => (
                    <li key={m.seat}>
                      {m.seat === s.room!.viewerSeat ? "我" : "对手"}：
                      {m.ready ? "已准备" : "未准备"}
                    </li>
                  ))}
                </ul>
                <button
                  disabled={locked || s.room.status !== "waiting"}
                  onClick={c.ready}
                >
                  {s.room.members.find((m) => m.seat === s.room?.viewerSeat)
                    ?.ready
                    ? "取消准备"
                    : "准备对战"}
                </button>
                <button
                  disabled={locked || s.room.status !== "waiting"}
                  onClick={c.leaveRoom}
                >
                  离开房间
                </button>
                {["closed", "technical_abort"].includes(s.room.status) && (
                  <button disabled={locked} onClick={c.dismissRoom}>
                    返回房间大厅
                  </button>
                )}
              </>
            )}
          </section>
          <section>
            <h2>继续对局与回放</h2>
            {s.remembered.matchId && (
              <button
                disabled={s.busy}
                onClick={() => void c.open(s.remembered.matchId!)}
              >
                返回当前对局
              </button>
            )}
            {s.status.pendingMatches.map((mid) => (
              <button
                key={mid}
                disabled={s.busy}
                onClick={() => void c.open(mid)}
              >
                恢复待确认对局
              </button>
            ))}
            <label>
              已有对局编号
              <input
                aria-label="已有对局编号"
                value={match}
                onChange={(e) => setMatch(e.target.value)}
              />
            </label>
            <button
              disabled={s.busy || !/^[a-f0-9]{32}$/.test(match)}
              onClick={() => void c.open(match)}
            >
              打开我的对局
            </button>
            <p className="online-muted">
              离开界面不会认输。历史回放只读，不能提交操作。
            </p>
          </section>
        </>
      )}
      {s.missing && (
        <aside>
          本机暂未匹配显示资源，将使用卡名和文字信息。缺图不会阻塞对战。
        </aside>
      )}
    </main>
  );
}
