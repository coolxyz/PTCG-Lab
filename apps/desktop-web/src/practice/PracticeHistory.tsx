import { useState } from "react";
import type { PracticeController, PracticeState } from "./controller";

export function PracticeHistory({
  state,
  controller,
}: {
  state: PracticeState;
  controller: PracticeController;
}) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const name = (id: string) =>
    state.session?.opponents.find((o) => o.id === id)?.name ||
    state.revisions.find((r) => "revision:" + r.id === id)?.name ||
    "历史 AI 卡组";
  const active = state.matches.filter((m) => m.status === "playing").length;
  const rows = state.matches.filter(
    (m) =>
      (filter === "all" ||
        (filter === "active") === (m.status === "playing")) &&
      `${name(m.opponent)} ${m.id}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(rows.length / 6));
  const current = Math.min(page, pages - 1);
  return (
    <section
      className="practice-panel practice-history"
      aria-labelledby="practice-history-heading"
    >
      <div className="practice-history-heading">
        <div>
          <span className="practice-step">YOUR PRACTICE</span>
          <h2 id="practice-history-heading">
            我的对局 <small>{state.matches.length}</small>
          </h2>
        </div>
        <p>继续练习，或回顾每一步决策。</p>
      </div>
      <div className="practice-history-tools">
        <div
          className="practice-filters"
          role="group"
          aria-label="练习状态筛选"
        >
          {[
            ["all", "全部", state.matches.length],
            ["active", "进行中", active],
            ["done", "已结束", state.matches.length - active],
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
          aria-label="搜索练习对局"
          placeholder="搜索对手卡组或对局编号…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
      </div>
      {rows.length ? (
        <ul className="practice-match-list" aria-label="练习对局记录">
          {rows.slice(current * 6, (current + 1) * 6).map((m) => (
            <li key={m.id}>
              <span className="practice-match-icon" aria-hidden="true">
                {m.status === "playing" ? "↗" : "▹"}
              </span>
              <div className="practice-match-info">
                <div>
                  <strong>{name(m.opponent)}</strong>
                  <span
                    className={`practice-badge ${m.status === "playing" ? "is-active" : ""}`}
                  >
                    {m.status === "playing" ? "进行中" : "已结束"}
                  </span>
                </div>
                <p>
                  已记录 {m.seq} 步 · 对局 {m.id.slice(0, 8)}
                </p>
              </div>
              <button
                className={`workspace-button ${m.status === "playing" ? "" : "is-secondary"}`}
                disabled={state.busy}
                onClick={() =>
                  void (m.status === "playing"
                    ? controller.open(m.id)
                    : controller.openReplay(m.id))
                }
              >
                {m.status === "playing" ? "继续对局" : "观看回放"}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="practice-empty">
          <span aria-hidden="true">◎</span>
          <h3>
            {state.matches.length ? "没有符合条件的练习" : "从第一场练习开始"}
          </h3>
          <p>
            {state.matches.length
              ? "试试其他状态或更短的关键词。"
              : "选择卡组与 AI 对手，开始后会在这里保存对局记录。"}
          </p>
          {state.matches.length > 0 && (
            <button
              className="workspace-button is-secondary"
              onClick={() => {
                setSearch("");
                setFilter("all");
                setPage(0);
              }}
            >
              清除练习筛选
            </button>
          )}
        </div>
      )}
      <div className="practice-history-footer">
        <span>
          {rows.length
            ? `${current * 6 + 1}–${Math.min((current + 1) * 6, rows.length)} / ${rows.length} 场`
            : "0 场"}
        </span>
        <nav aria-label="练习记录分页">
          <button
            className="workspace-button is-secondary"
            aria-label="上一页练习"
            disabled={!current}
            onClick={() => setPage(current - 1)}
          >
            ←
          </button>
          <span>
            {current + 1} / {pages}
          </span>
          <button
            className="workspace-button is-secondary"
            aria-label="下一页练习"
            disabled={current + 1 >= pages}
            onClick={() => setPage(current + 1)}
          >
            →
          </button>
        </nav>
      </div>
    </section>
  );
}
