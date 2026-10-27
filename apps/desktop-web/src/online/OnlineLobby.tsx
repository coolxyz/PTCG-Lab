import { useEffect, useState } from "react";
import { type OnlineController, type State } from "./controller";
import { SharedDeckPicker } from "./SharedDeckPicker";
import { MatchLibrary } from "./MatchLibrary";

export function OnlineLobby({
  controller: c,
  state: s,
  product,
}: {
  controller: OnlineController;
  state: State;
  product: boolean;
}) {
  const [name, setName] = useState("训练家");
  const [deck, setDeck] = useState("");
  const [invite, setInvite] = useState("");
  const [copied, setCopied] = useState("");
  const locked =
    s.busy ||
    !!s.remembered.job ||
    !!s.status?.reauthenticationRequired ||
    !!s.status?.productPending?.length;
  const ready = !!s.remembered.revision;
  const room = s.room;
  useEffect(() => setCopied(""), [room?.inviteCode]);
  const accepting = !!s.caps?.acceptingMatches;
  return (
    <section className="workspace-page online-lobby">
      <header className="page-heading lobby-heading">
        <div>
          <p className="eyebrow">PTCG LAB / PLAY TOGETHER</p>
          <h1>对战大厅</h1>
          <p className="online-muted">带上你的卡组，邀请朋友开启下一场对决。</p>
        </div>
        <a
          className="lobby-link-button"
          href={product ? "/#online" : "/online.html"}
          target="_blank"
          rel="noopener noreferrer"
        >
          打开另一个窗口 ↗
        </a>
      </header>
      <section className="lobby-connection" aria-label="连接与身份">
        <div className="lobby-connection-main">
          <div>
            <span
              className={`lobby-dot ${s.connected && s.status?.identity && !s.status.reauthenticationRequired ? "is-online" : ""}`}
            />
            <strong>
              {s.status?.reauthenticationRequired
                ? "需要重新认证"
                : s.status?.identity
                  ? "游客身份已连接"
                  : "连接与身份"}
            </strong>
            <span className="online-muted">{s.profile || "选择服务器"}</span>
          </div>
          <div className="lobby-actions">
            <span className="lobby-sync" role="status">
              {s.busy ? "正在处理…" : ""}
            </span>
            <button
              className="lobby-secondary"
              disabled={s.busy}
              onClick={c.refresh}
            >
              刷新连接状态
            </button>
          </div>
        </div>
        <details open={!s.status?.identity || undefined}>
          <summary>服务器与身份设置</summary>
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
          {!s.status?.identity && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void c.guest(name.trim());
              }}
            >
              <label>
                玩家名称
                <input
                  aria-label="玩家名称"
                  value={name}
                  maxLength={40}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <button disabled={s.busy || !s.connected || !name.trim()}>
                创建游客身份
              </button>
            </form>
          )}
          <p className="online-muted">
            身份与对局记录保存在本机后台。请保留凭据目录，以便继续对局和观看回放。
          </p>
        </details>
      </section>
      {s.error && <p role="alert">{s.error}</p>}
      {!s.remembered.job &&
        s.status?.productPending?.map((p) => (
          <div className="lobby-notice" key={p.requestId} role="status">
            <p>上次卡组提交尚未确认，请恢复原请求后继续。</p>
            <button disabled={s.busy} onClick={() => void c.recoverProduct(p)}>
              恢复原卡组提交
            </button>
          </div>
        ))}
      {s.remembered.job && (
        <div className="lobby-notice" role="status">
          <p>上次大厅操作尚未确认，请重试原请求。</p>
          <button disabled={s.busy} onClick={c.retryLobby}>
            重试大厅操作
          </button>
        </div>
      )}
      {s.status?.identity && (
        <>
          <div className="lobby-play-grid">
            <section
              className="lobby-panel lobby-deck-panel"
              aria-labelledby="deck-heading"
            >
              <div className="lobby-panel-heading">
                <div>
                  <span className="lobby-step">01 / 准备</span>
                  <h2 id="deck-heading">选择卡组</h2>
                </div>
                <span
                  className={`lobby-badge ${ready ? "is-ready" : ""}`}
                  role="status"
                >
                  {ready ? "卡组版本已就绪" : "尚未提交卡组"}
                </span>
              </div>
              <label>
                规则版本
                <select
                  aria-label="规则版本"
                  value={s.rule}
                  disabled={locked || !!room}
                  onChange={(e) => c.rule(e.target.value)}
                >
                  {s.caps?.ruleReleaseIds.map((r) => (
                    <option key={r}>{r}</option>
                  ))}
                </select>
              </label>
              {product ? (
                <SharedDeckPicker
                  controller={c}
                  disabled={locked || !!room || !s.rule}
                />
              ) : (
                <>
                  <label>
                    卡组清单
                    <textarea
                      aria-label="卡组清单"
                      rows={6}
                      value={deck}
                      disabled={locked || !!room}
                      onChange={(e) => setDeck(e.target.value)}
                      placeholder={
                        '[{"printingId":"卡牌印刷编号","quantity":4}]'
                      }
                    />
                  </label>
                  <p className="online-muted">
                    粘贴卡组的 JSON 清单，仅提交卡牌编号与数量。
                  </p>
                  <button
                    disabled={locked || !s.rule || !!room || !deck.trim()}
                    onClick={() => void c.deck(deck)}
                  >
                    校验并提交卡组
                  </button>
                </>
              )}
              {room && (
                <p className="online-muted">
                  卡组已锁定在当前房间，离开房间后可更换。
                </p>
              )}
            </section>
            <section
              className="lobby-panel lobby-room-panel"
              aria-labelledby="room-heading"
            >
              <div className="lobby-panel-heading">
                <div>
                  <span className="lobby-step">02 / 邀请</span>
                  <h2 id="room-heading">
                    {room ? "你的对战房间" : "与朋友对战"}
                  </h2>
                </div>
                <span className="lobby-badge">双人对战</span>
              </div>
              {!room ? (
                <>
                  <div className="lobby-create">
                    <div className="lobby-room-art" aria-hidden="true">
                      <span />
                      <b>VS</b>
                      <span />
                    </div>
                    <h3>一张房间码，开启一场对决</h3>
                    <p className="online-muted">
                      创建房间，将邀请码发给朋友。双方准备后开始对战。
                    </p>
                    <button
                      className="lobby-wide"
                      disabled={locked || !ready || !accepting}
                      onClick={c.create}
                    >
                      创建邀请房间 <span aria-hidden="true">→</span>
                    </button>
                  </div>
                  <form
                    className="lobby-join"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (!locked && ready && accepting && invite.length === 10)
                        void c.join(invite);
                    }}
                  >
                    <label htmlFor="lobby-invite">已经有邀请？输入房间码</label>
                    <div className="lobby-inline">
                      <input
                        id="lobby-invite"
                        aria-label="房间码"
                        placeholder="10 位房间码"
                        value={invite}
                        disabled={locked}
                        onChange={(e) =>
                          setInvite(
                            e.target.value.replace(/\s/g, "").toUpperCase(),
                          )
                        }
                        maxLength={10}
                        autoComplete="off"
                        autoCapitalize="characters"
                        spellCheck={false}
                      />
                      <button
                        className="lobby-secondary"
                        disabled={
                          locked || !ready || !accepting || invite.length !== 10
                        }
                      >
                        加入房间
                      </button>
                    </div>
                  </form>
                  <p className="lobby-hint">
                    {!accepting
                      ? "服务器暂不接受新对局，请稍后刷新连接状态。"
                      : !ready
                        ? "先在左侧校验并提交卡组，即可创建或加入房间。"
                        : "卡组已准备好，可以创建房间或接受邀请。"}
                  </p>
                </>
              ) : (
                <>
                  <div className="lobby-invitation">
                    <span className="online-muted">将房间码分享给朋友</span>
                    <strong
                      className="lobby-invite-code"
                      data-testid="invite-code"
                    >
                      {room.inviteCode}
                    </strong>
                    <button
                      className="lobby-secondary"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(room.inviteCode);
                          setCopied("房间码已复制");
                        } catch {
                          setCopied("未能复制，请选中上方房间码手动复制。");
                        }
                      }}
                    >
                      复制房间码
                    </button>
                    <p className="lobby-copy-status" role="status">
                      {copied}
                    </p>
                  </div>
                  <div className="lobby-seats">
                    {["PLAYER1", "PLAYER2"].map((seat) => {
                      const member = room.members.find((m) => m.seat === seat);
                      return (
                        <div
                          key={seat}
                          className={`lobby-seat ${member?.ready ? "is-ready" : ""}`}
                        >
                          <span className="lobby-avatar" aria-hidden="true">
                            {seat === room.viewerSeat ? "我" : "友"}
                          </span>
                          <strong>
                            {seat === room.viewerSeat
                              ? "我"
                              : member
                                ? "对手"
                                : "等待朋友加入"}
                          </strong>
                          <span>
                            {member
                              ? member.ready
                                ? "已准备"
                                : "未准备"
                              : "分享房间码邀请"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  <p className="lobby-hint" role="status">
                    {(
                      {
                        waiting: "等待双方准备",
                        initializing: "正在初始化对局",
                        initialized: "对局已创建",
                        closed: "房间已关闭",
                        technical_abort: "初始化失败",
                      } as Record<string, string>
                    )[room.status] || room.status}
                  </p>
                  <div className="lobby-actions">
                    <button
                      disabled={locked || room.status !== "waiting"}
                      onClick={c.ready}
                    >
                      {room.members.find((m) => m.seat === room.viewerSeat)
                        ?.ready
                        ? "取消准备"
                        : "准备对战"}
                    </button>
                    <button
                      className="lobby-secondary"
                      disabled={locked || room.status !== "waiting"}
                      onClick={c.leaveRoom}
                    >
                      离开房间
                    </button>
                    {["closed", "technical_abort"].includes(room.status) && (
                      <button disabled={locked} onClick={c.dismissRoom}>
                        返回房间大厅
                      </button>
                    )}
                  </div>
                </>
              )}
            </section>
          </div>
          <MatchLibrary controller={c} state={s} />
        </>
      )}
      {s.missing && (
        <aside className="lobby-notice">
          本机暂未匹配显示资源，将使用卡名和文字信息。缺图不会阻塞对战。
        </aside>
      )}
      <footer className="lobby-footer">
        离开界面不会认输 · 回放仅展示你可见的对局信息
      </footer>
    </section>
  );
}
