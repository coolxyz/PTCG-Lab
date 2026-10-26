import { useState } from "react";
import { type OnlineController, type State } from "./controller";

type Match = NonNullable<NonNullable<State["status"]>["recentMatches"]>[number];
const result = (m: Match) =>
  !m.done
    ? "进行中"
    : m.winner === m.viewerSeat
      ? "胜利"
      : ["PLAYER1", "PLAYER2"].includes(m.winner || "")
        ? "落败"
        : "已结束";
const pageSize = 6;
export function MatchLibrary({
  controller: c,
  state: s,
}: {
  controller: OnlineController;
  state: State;
}) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [match, setMatch] = useState("");
  const rows = s.status?.recentMatches || [];
  const active = rows.filter((m) => !m.done).length;
  const pending = s.status?.pendingMatches || [];
  const filtered = rows.filter(
    (m) =>
      (filter === "all" || (filter === "active" ? !m.done : m.done)) &&
      m.matchId.includes(search.trim().toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(page, pages - 1);
  return (
    <section
      className="lobby-panel lobby-history"
      aria-labelledby="history-heading"
    >
      <div className="lobby-panel-heading">
        <div>
          <span className="lobby-step">YOUR MATCHES</span>
          <h2 id="history-heading">
            我的对局 <span className="lobby-count">{rows.length}</span>
          </h2>
        </div>
        <p className="online-muted">继续未完的对战，重温每一次交锋。</p>
      </div>
      <div className="lobby-history-tools">
        <div className="lobby-filters" role="group" aria-label="对局状态筛选">
          {[
            ["all", "全部", rows.length],
            ["active", "进行中", active],
            ["done", "已结束", rows.length - active],
          ].map(([key, label, count]) => (
            <button
              key={key}
              aria-pressed={filter === key}
              onClick={() => {
                setFilter(String(key));
                setPage(0);
              }}
            >
              {label} <span>{count}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          aria-label="搜索对局编号"
          placeholder="搜索对局编号…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
      </div>
      {pending.length > 0 && (
        <div className="lobby-notice">
          <strong>有 {pending.length} 场对局的操作等待确认</strong>
          <div className="lobby-actions">
            {pending.map((mid) => (
              <button
                key={mid}
                disabled={s.busy}
                onClick={() => void c.open(mid)}
              >
                恢复待确认对局 · {mid.slice(0, 8)}
              </button>
            ))}
          </div>
        </div>
      )}
      {filtered.length ? (
        <ul className="lobby-match-list" aria-label="本机对局记录">
          {filtered
            .slice(current * pageSize, (current + 1) * pageSize)
            .map((item) => (
              <li key={item.matchId} className="lobby-match">
                <span
                  className={`lobby-match-symbol ${!item.done ? "is-active" : ""}`}
                  aria-hidden="true"
                >
                  {!item.done ? "↗" : "▹"}
                </span>
                <div className="lobby-match-info">
                  <div className="lobby-match-title">
                    <strong>
                      {!item.done ? "进行中的对战" : "已结束的对战"}
                    </strong>
                    <span
                      className={`lobby-badge ${!item.done || item.winner === item.viewerSeat ? "is-ready" : ""}`}
                    >
                      {result(item)}
                    </span>
                    {pending.includes(item.matchId) && (
                      <span className="lobby-badge">待确认</span>
                    )}
                  </div>
                  <p>
                    {item.viewerSeat === "PLAYER1" ? "玩家 1" : "玩家 2"}{" "}
                    <span>·</span> 已记录 {item.matchSeq} 步
                  </p>
                  <details>
                    <summary>编号 {item.matchId.slice(0, 8)}…</summary>
                    <code>{item.matchId}</code>
                  </details>
                </div>
                <button
                  className={item.done ? "lobby-secondary" : ""}
                  disabled={s.busy}
                  onClick={() =>
                    void (item.done
                      ? c.openReplay(item.matchId)
                      : c.open(item.matchId))
                  }
                >
                  {item.done ? "观看回放" : "继续对局"}
                </button>
              </li>
            ))}
        </ul>
      ) : (
        <div className="lobby-empty">
          <span aria-hidden="true">◎</span>
          <h3>
            {rows.length ? "没有符合条件的对局" : "你的第一场对战，从这里开始"}
          </h3>
          <p className="online-muted">
            {rows.length
              ? "试试其他状态，或更短的对局编号。"
              : "创建或加入房间后，对局会自动记录在这里。"}
          </p>
          {rows.length > 0 && (
            <button
              className="lobby-secondary"
              onClick={() => {
                setSearch("");
                setFilter("all");
                setPage(0);
              }}
            >
              清除筛选
            </button>
          )}
        </div>
      )}
      <div className="lobby-history-bottom">
        <span className="online-muted">
          {filtered.length
            ? `${current * pageSize + 1}–${Math.min((current + 1) * pageSize, filtered.length)} / ${filtered.length} 场`
            : "0 场"}
        </span>
        <nav aria-label="对局记录分页">
          <button
            className="lobby-secondary"
            aria-label="上一页对局"
            disabled={!current}
            onClick={() => setPage(current - 1)}
          >
            ←
          </button>
          <span>
            {current + 1} / {pages}
          </span>
          <button
            className="lobby-secondary"
            aria-label="下一页对局"
            disabled={current + 1 >= pages}
            onClick={() => setPage(current + 1)}
          >
            →
          </button>
        </nav>
      </div>
      <p className="online-muted">
        显示本机当前服务器、当前身份已访问的对局，状态以最近同步为准。其他设备或旧缓存已移除的对局，可通过编号找回。
      </p>
      <details className="lobby-recover">
        <summary>通过编号找回对局</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!s.busy && /^[a-f0-9]{32}$/.test(match.trim().toLowerCase()))
              void c.open(match.trim().toLowerCase());
          }}
        >
          <label htmlFor="lobby-match-id">已有对局编号</label>
          <div className="lobby-inline">
            <input
              id="lobby-match-id"
              aria-label="已有对局编号"
              value={match}
              placeholder="输入完整的 32 位对局编号"
              onChange={(e) => setMatch(e.target.value)}
            />
            <button
              className="lobby-secondary"
              disabled={
                s.busy || !/^[a-f0-9]{32}$/.test(match.trim().toLowerCase())
              }
            >
              打开我的对局
            </button>
          </div>
        </form>
        <p className="online-muted">只能打开当前身份参与的对局。</p>
      </details>
    </section>
  );
}
