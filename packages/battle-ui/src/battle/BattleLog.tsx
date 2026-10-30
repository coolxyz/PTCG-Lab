import { useLayoutEffect, useRef, useState } from "react";
import { CardDescription } from "../CardDescription";
import { eventText, lookupCard, playerLabel } from "./presentation";
import type { BattleView, CardInfo, EngineCard, PlayerSeat } from "./types";
import "./log.css";

type Entry = NonNullable<BattleView["events"]>[number];

function PublicCard({
  card,
  cards,
  onOpen,
}: {
  card: EngineCard;
  cards: Record<string, CardInfo>;
  onOpen: (card: EngineCard) => void;
}) {
  const info = lookupCard(card, cards),
    name = info?.cnName || card.name;
  return (
    <button
      type="button"
      className="log-card"
      onClick={() => onOpen(card)}
      aria-label={`查看记录卡牌：${name}`}
    >
      {info?.image.url && (
        <img
          src={info.image.url}
          alt=""
          loading="lazy"
          onError={(e) => {
            e.currentTarget.style.display = "none";
          }}
        />
      )}
      <span>{name}</span>
    </button>
  );
}

function RecordCardDialog({
  card,
  info,
  onClose,
}: {
  card: EngineCard;
  info?: CardInfo;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const focused = document.activeElement as HTMLElement | null,
      element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      if (focused?.isConnected) focused.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="log-card-dialog"
      aria-label="记录卡牌详情"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>{info?.cnName || card.name}</h2>
        <button className="secondary" onClick={onClose}>
          关闭卡牌详情
        </button>
      </header>
      <p>这是该步骤公开的卡牌记录，不代表它仍在原区域或当前手牌中。</p>
      {info?.image.url && <img src={info.image.url} alt={info.cnName} />}
      <CardDescription details={info?.chineseDetails} types={{}} />
      {!info?.chineseDetails?.sections?.length &&
        card.attacks?.map((a, i) => (
          <p key={i}>
            {a.name}：{a.text}
          </p>
        ))}
    </dialog>
  );
}

/** A reading receipt is local UI state; it never blocks or acknowledges a game command. */
export function BattleLog({
  view,
  cards,
  viewerSeat,
  opponentLabel,
  replay,
}: {
  view: BattleView;
  cards: Record<string, CardInfo>;
  viewerSeat: PlayerSeat;
  opponentLabel: string;
  replay: boolean;
}) {
  const entries = [...(view.events || [])].sort((a, b) => b.seq - a.seq);
  const latest = entries[0]?.seq ?? -1;
  const [read, setRead] = useState(latest - 1);
  const [cutoff, setCutoff] = useState<number | null>(null);
  const [pausedTurn, setPausedTurn] = useState<number | null>(null);
  const [filter, setFilter] = useState("all");
  const [expanded, setExpanded] = useState<Map<number, boolean>>(
    () => new Map(),
  );
  const [focus, setFocus] = useState<EngineCard | null>(null);
  const list = useRef<HTMLOListElement>(null);
  const label = (actor: string) =>
    playerLabel(actor, viewerSeat, opponentLabel);
  const pending = entries.filter((e) => e.seq > (cutoff ?? latest)).length;
  const unread = entries.filter((e) => e.seq > read).length;
  const currentTurn = !replay && cutoff !== null
    ? pausedTurn
    : view.observation.turn_number;
  const visible = entries.filter(
    (e) =>
      (replay || cutoff === null || e.seq <= cutoff) &&
      (filter === "all" ||
        (filter === "opponent" && label(e.actor) === opponentLabel) ||
        (filter === "reveal" && !!e.cardGroups?.length) ||
        (filter === "turn" && e.turn === currentTurn)),
  );
  function pause() {
    if (replay || cutoff !== null) return;
    setCutoff(latest);
    setPausedTurn(view.observation.turn_number);
    // Preserve the automatically expanded entry while the reader pauses.
    setExpanded((old) => {
      const next = new Map(old);
      if (visible[0] && !next.has(visible[0].seq)) next.set(visible[0].seq, true);
      return next;
    });
  }
  function resume() {
    setCutoff(null);
    setRead(latest);
    setExpanded(new Map());
    list.current?.scrollTo({ top: 0 });
  }
  function details(e: Entry) {
    return (
      <div className="log-entry-body">
        {e.cardGroups?.map((group, i) => (
          <div className="log-card-group" key={i}>
            <strong>{group.label}</strong>
            <div>
              {group.cards.map((card, j) => (
                <PublicCard
                  key={j}
                  card={card}
                  cards={cards}
                  onOpen={(c) => {
                    setFocus(c);
                    pause();
                  }}
                />
              ))}
            </div>
          </div>
        ))}
        {!!e.coins?.length && (
          <p className="log-coins">
            投币结果：
            {e.coins
              .map((c, i) => `${i + 1}. ${c === "heads" ? "正面" : "反面"}`)
              .join(" · ")}
          </p>
        )}
        {!!e.changes?.length && (
          <div className="log-outcomes">
            <strong>结算后的可见变化</strong>
            <ul>
              {e.changes.map((c, i) => (
                <li key={i}>{eventText(c, cards)}</li>
              ))}
            </ul>
          </div>
        )}
        {!e.changes?.length && !e.cardGroups?.length && !e.coins?.length && (
          <p>本步骤未记录其他公开变化。</p>
        )}
        {e.explanation && <p className="log-note">{e.explanation}</p>}
      </div>
    );
  }
  return (
    <section className="battle-log battle-information" aria-label="对局信息">
      <header className="log-heading">
        <div>
          <h3>对局信息</h3>
          <small>动作、展示与结算 · 裁定记录</small>
        </div>
        {!replay && (
          <button
            className="secondary"
            onClick={() => setRead(latest)}
            disabled={!unread}
          >
            已阅{unread ? ` ${unread} 条` : ""}
          </button>
        )}
      </header>
      <div className="log-filters">
        <label>
          记录范围
          <select
            aria-label="对局记录范围"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="all">全部记录</option>
            <option value="turn">本回合</option>
            <option value="opponent">{opponentLabel}的动作</option>
            <option value="reveal">涉及公开卡牌</option>
          </select>
        </label>
        {!replay && (
          <button
            className="secondary"
            aria-pressed={cutoff === null}
            onClick={() => (cutoff === null ? pause() : resume())}
          >
            {cutoff === null ? "暂停跟随" : "跟随最新"}
          </button>
        )}
      </div>
      <div className="log-notification" aria-live="polite" aria-atomic="true">
        {!replay && pending > 0 ? (
          <button onClick={resume}>{pending} 条新记录 · 查看最新</button>
        ) : cutoff !== null && !replay ? (
          "已暂停跟随，可以安心阅读。"
        ) : replay ? (
          "显示到当前回放步骤，不包含后续信息。"
        ) : (
          "自动跟随最新步骤"
        )}
      </div>
      <ol
        ref={list}
        onScroll={(e) => {
          if (!replay && cutoff === null && e.currentTarget.scrollTop > 24)
            pause();
        }}
      >
        {visible.map((e, index) => {
          const open =
            expanded.get(e.seq) ?? (index === 0 && (replay || cutoff === null));
          return (
            <li
              key={e.seq}
              className={label(e.actor) === opponentLabel ? "log-opponent" : ""}
            >
              <button
                className="log-entry-toggle"
                aria-expanded={open}
                onClick={() => {
                  pause();
                  setExpanded((old) => {
                    const next = new Map(old);
                    next.set(e.seq, !open);
                    return next;
                  });
                }}
              >
                <small>
                  {e.turn !== undefined ? `回合 ${e.turn} · ` : ""}#{e.seq} ·{" "}
                  {label(e.actor)}
                  {!replay && e.seq > read ? " · 未读" : ""}
                </small>
                <strong>
                  {eventText(
                    e.text,
                    cards,
                    e.cardGroups?.flatMap((g) => g.cards),
                  )}
                </strong>
                <span className="log-entry-hint">
                  {open
                    ? "收起详情"
                    : `查看详情${e.cardGroups?.length ? " · 含公开卡牌" : ""}`}
                </span>
              </button>
              {open && details(e)}
            </li>
          );
        })}
      </ol>
      {!visible.length && <p className="log-empty">暂无符合筛选的记录。</p>}
      <p className="log-note">
        展示记录只说明当时公开的信息；“已阅”不会暂停对局。
      </p>
      {focus && (
        <RecordCardDialog
          card={focus}
          info={lookupCard(focus, cards)}
          onClose={() => setFocus(null)}
        />
      )}
    </section>
  );
}
