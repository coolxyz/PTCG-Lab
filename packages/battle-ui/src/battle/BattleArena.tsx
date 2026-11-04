import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Flag,
  History,
  ListChecks,
  Swords,
} from "lucide-react";
import { Tabletop } from "../Tabletop";
import { DeckBrowser } from "../DeckBrowser";
import { BattleScreen } from "./BattleScreen";
import { BattleLog } from "./BattleLog";
import type { AppearanceStorage } from "./appearance";
import { eventText, lookupCard, playerLabel, skillName } from "./presentation";
import type {
  BattleView,
  CardInfo,
  Command,
  Option,
  ReplayCheckpoint,
  PlayerSeat,
} from "./types";
import "../battle.css";

export type ReplayDisplay = {
  frame: BattleView;
  seq: number;
  end: number;
  timeline: ReplayCheckpoint[];
  playing: boolean;
  speed: number;
  animate: boolean;
  onSeek: (seq: number) => void;
  onPlayingChange: (playing: boolean) => void;
  onSpeedChange: (speed: number) => void;
  onClose: () => void;
};

export type BattleArenaProps = {
  mobile?: boolean;
  appearanceStorage?: AppearanceStorage;
  view: BattleView;
  cards: Record<string, CardInfo>;
  viewerSeat?: PlayerSeat;
  opponentLabel?: string;
  busy?: boolean;
  error?: string;
  pendingCommand?: boolean;
  replay?: ReplayDisplay;
  statusLabel?: ReactNode;
  /** Optional controls supplied by a mode controller; never implemented here. */
  modeControls?: ReactNode;
  onSubmit?: (choice: Command["choice"]) => void;
  onRetry?: () => void;
  onAcquireControl?: () => void;
  onRefresh: () => void;
  onLeave: () => void;
  onOpenReplay?: () => void;
  onResign?: () => void | Promise<void>;
  onCoinPlaying?: (playing: boolean) => void;
};
const mobilePanels = [
  ["board", "场面", Swords],
  ["log", "记录", History],
] as const;
type MobilePanel = (typeof mobilePanels)[number][0];

const actionNames: Record<string, string> = {
  PlayPokemonAction: "放置宝可梦",
  EvolvePokemonAction: "进化",
  AttachEnergyAction: "附加能量",
  UseAbilityAction: "使用特性",
  UseSupporterAction: "使用支援者",
  UseItemAction: "使用物品",
  UseToolAction: "附加道具",
  PutStadiumAction: "放置竞技场",
  UseStadiumAction: "使用竞技场",
  DiscardStadiumAction: "弃置竞技场",
  AttackAction: "攻击",
  RetreatAction: "撤退",
  PassTurn: "结束回合",
};
const phases: Record<string, string> = {
  choose_order: "选择先后攻",
  mulligan: "确认重抽",
  active: "选择战斗宝可梦",
  bench: "布置备战宝可梦",
  bonus_bench: "补充备战宝可梦",
  mulligan_bonus: "选择补偿抽牌数量",
  playing: "对战中",
};
/** Shared display boundary: no API calls, storage, AI scheduling or game rules. */
export function BattleArena({
  mobile = false,
  appearanceStorage,
  view,
  cards: names,
  viewerSeat = "PLAYER1",
  opponentLabel = "对手",
  busy = false,
  error = "",
  pendingCommand = false,
  replay,
  statusLabel,
  modeControls,
  onSubmit,
  onRetry,
  onAcquireControl,
  onRefresh,
  onLeave,
  onOpenReplay,
  onResign,
  onCoinPlaying,
}: BattleArenaProps) {
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [resignConfirm, setResignConfirm] = useState(false);
  const [panel, setPanel] = useState<MobilePanel>("board");
  const [logRequest, setLogRequest] = useState<{ seq: number; turn: number } | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsDialog = useRef<HTMLDialogElement>(null);
  const prompted = useRef<string | undefined>(undefined);
  const frame = replay?.frame;
  const shown = frame || view;
  const d = shown.decision;
  const replaySeq = replay?.seq ?? 0;
  const replayEnd = replay?.end ?? 0;
  const timeline = replay?.timeline ?? [];
  const autoplay = replay?.playing ?? false;
  const speed = replay?.speed ?? 1;
  const animateReplay = replay?.animate ?? false;
  const readOnly =
    busy ||
    !!frame ||
    pendingCommand ||
    shown.done ||
    !onSubmit ||
    (!!d?.actor && d.actor !== viewerSeat);
  const forcedDecision =
    mobile &&
    !readOnly &&
    d &&
    (shown.phase !== "playing" ||
      d.kind === "selection" ||
      d.options.some((o) =>
        ["NumberOption", "ChooseCardAction"].includes(o.actionType),
      ))
      ? d.id
      : undefined;
  useEffect(() => {
    prompted.current = undefined;
    setPanel("board");
    setLogRequest(null);
    setMenuOpen(false);
    setActionsOpen(false);
  }, [shown.matchId, !!frame, viewerSeat]);
  useEffect(() => {
    if (!menuOpen && forcedDecision && prompted.current !== forcedDecision) {
      prompted.current = forcedDecision;
      setActionsOpen(true);
    }
  }, [forcedDecision, menuOpen]);
  useLayoutEffect(() => {
    const dialog = actionsDialog.current;
    if (!dialog) return;
    if (actionsOpen && !frame && !shown.done) dialog.showModal();
    else dialog.close();
  }, [actionsOpen, !!frame, shown.done]);
  const cn = (name: string | undefined) =>
    name ? names[name]?.cnName || name : "";
  const actorName = (actor: string | null) =>
    playerLabel(actor, viewerSeat, opponentLabel);
  const opponentEvents = mobile && !frame
    ? (shown.events ?? []).filter((event) => actorName(event.actor) === opponentLabel)
    : [];
  const lastOpponentEvent = opponentEvents.at(-1);
  const opponentAction = opponentEvents.filter(
    (event) =>
      event.turn === lastOpponentEvent?.turn &&
      event.actionType !== "PassTurn" &&
      event.text !== "结束回合" &&
      event.kind !== "result",
  ).at(-1) ?? lastOpponentEvent;
  const showReplay = (seq: number) => replay?.onSeek(seq);
  const submit = (choice: Command["choice"]) => {
    if (readOnly) return;
    if (mobile) setActionsOpen(false);
    onSubmit?.(choice);
  };
  useEffect(() => {
    setSelected([]);
    setFilter("");
    setResignConfirm(false);
  }, [
    shown.matchId,
    shown.stateVersion,
    shown.matchSeq,
    d?.id,
    !!frame,
    viewerSeat,
  ]);
  function optionText(o: Option) {
    const visible = [
      shown.observation.self,
      shown.observation.opponent,
    ].flatMap((s) => [...s.active, ...s.bench, ...(s.hand || [])]);
    const source =
      visible.find(
        (c) => c.ref && c.ref === (o.sourceRef || o.active_pokemonRef),
      ) || visible.find((c) => c.id && c.id === (o.sourceId || o.active_pokemonId));
    const skillSource = o.effectSource || source;
    const info = skillSource ? lookupCard(skillSource, names) : lookupCard(
      { id: o.sourceId, name: o.source || o.active_pokemon || "" }, names,
    );
    const skill = (name: string, kind: "attack" | "ability") =>
      skillName(name, kind, skillSource, info);
    if (o.actionType === "ChooseCardAction") {
      const counts = new Map<string, number>();
      for (const name of o.chosen || [])
        counts.set(name, (counts.get(name) || 0) + 1);
      return counts.size
        ? "弃置能量：" +
            [...counts]
              .map(([name, quantity]) => `${cn(name)} ×${quantity}`)
              .join("、")
        : "无需弃置能量，继续撤退";
    }
    if (o.actionType === "NumberOption") return o.label ?? `选择 ${o.value}`;
    if (o.actionType === "SetupOption") {
      if (o.kind === "choose_order")
        return o.value === "first" ? "选择先攻" : "选择后攻";
      if (o.kind === "mulligan_bonus") return `补偿抽 ${o.value} 张`;
      return "已查看重抽手牌，继续";
    }
    const position = (pos?: string, index?: number) =>
      pos?.endsWith("BENCH")
        ? `（备战 ${index}）`
        : pos?.endsWith("ACTIVE")
          ? "（战斗区）"
          : "";
    return `${actionNames[o.actionType] || o.actionType}${o.source ? " · " + (lookupCard({id:o.sourceId,name:o.source}, names)?.cnName || cn(o.source)) + position(o.sourcePosition, o.sourceIndex) : ""}${o.target ? " → " + (lookupCard({id:o.targetId,name:o.target}, names)?.cnName || cn(o.target)) + position(o.targetPosition, o.targetIndex) : ""}${o.attack ? " · " + skill(o.attack.name, "attack") + (o.attack.damage ? " / " + o.attack.damage : "") : ""}${o.effectSource ? "（来自" + (lookupCard(o.effectSource, names)?.cnName || o.effectSource.name) + "）" : ""}${o.ability ? " · " + skill(o.ability, "ability") : ""}${o.active_pokemon ? " · " + cn(o.active_pokemon) : ""}`;
  }
  const previousTurn = timeline.filter((point) => point.seq < replaySeq).at(-1);
  const nextTurn = timeline.find((point) => point.seq > replaySeq);
  const currentTurn = timeline.filter((point) => point.seq <= replaySeq).at(-1);
  const passTurn =
    mobile && !frame && d?.kind === "options"
      ? d.options.find((o) => o.actionType === "PassTurn")
      : undefined;
  const turnActor = actorName(shown.observation.turn);
  const stepLabel = frame
    ? `回放 · 第 ${replaySeq} 步`
    : `${phases[shown.phase] || shown.phase} · 回合 ${shown.observation.turn_number} · 第 ${shown.matchSeq ?? shown.stateVersion} 步`;
  const turnLabel = frame
    ? "只读回放"
    : shown.done
      ? "对局结束"
      : pendingCommand
        ? "操作待确认"
        : busy
          ? "正在同步…"
          : !onSubmit
            ? `只读观察${turnActor === "系统" ? "" : ` · ${turnActor === "你" ? "我方" : turnActor}回合`}`
            : d && !readOnly
              ? "轮到你操作"
              : turnActor === opponentLabel
                ? `${opponentLabel}回合`
                : turnActor === "你"
                  ? "等待局面更新"
                  : `等待${opponentLabel}操作`;
  function changeMenu(open: boolean) {
    setMenuOpen(open);
    setResignConfirm(false);
  }
  const matchActions = (
    <>
      <button className="secondary" disabled={busy} onClick={onLeave}>
        返回大厅
      </button>
      {(!mobile || !frame) && (
        <button
          className="secondary"
          disabled={busy || !onOpenReplay}
          onClick={() => {
            if (mobile) changeMenu(false);
            onOpenReplay?.();
          }}
        >
          <History size={14} aria-hidden="true" /> 逐步回放
        </button>
      )}
      {onResign && !view.done && !frame && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => setResignConfirm(true)}
        >
          <Flag size={14} aria-hidden="true" /> 认输
        </button>
      )}
    </>
  );
  const resignPrompt = resignConfirm && onResign && (
    <div className="battle-confirm" role="alert">
      确认认输并结束本局？
      <button
        disabled={busy}
        onClick={async () => {
          if (mobile) changeMenu(false);
          await onResign();
          setResignConfirm(false);
        }}
      >
        确认认输
      </button>
      <button onClick={() => setResignConfirm(false)}>继续对战</button>
    </div>
  );
  const publicRecords = (
    <>
      {!!shown.setupEvents?.some((e) => e.kind === "mulligan") && (
        <details className="battle-public">
          <summary>开局重抽公开记录</summary>
          {shown.setupEvents
            .filter((e) => e.kind === "mulligan")
            .map((e, i) => (
              <p key={i}>
                {Object.entries(e.hands)
                  .map(
                    ([p, c]: any) =>
                      `${actorName(p)}：${c.map((x: any) => cn(x.name)).join("、")}`,
                  )
                  .join("；")}
              </p>
            ))}
        </details>
      )}
      {!!shown.publicReveals?.length && (
        <details className="battle-public">
          <summary>已公开检索卡牌</summary>
          {shown.publicReveals.map((e, i) => (
            <p key={i}>
              {actorName(e.actor)}：
              {e.cards.map((c: any) => cn(c.name)).join("、")}
            </p>
          ))}
        </details>
      )}
    </>
  );
  const decisionPanel = (
    <section
      className="decision-panel"
      aria-label="当前决策"
    >
      <div className="decision-heading">
        <h2>
          {frame
            ? "历史决策（只读）"
            : shown.done
              ? "本局已结束"
              : mobile && readOnly && !busy && !pendingCommand && d
                ? "当前动作（只读）"
                : d?.kind === "selection" && d.max === 0
                  ? "查看卡牌"
                  : d
                    ? "轮到你选择"
                    : `${opponentLabel} 正在操作…`}
        </h2>
        {passTurn && (
          <button
            className="secondary"
            disabled={readOnly}
            onClick={() => submit({ optionId: passTurn.id })}
          >
            结束回合
          </button>
        )}
      </div>
      {d?.kind === "selection" && (
        <>
          <p>
            {d.instruction || phases[shown.phase]}
            {d.source ? " · " + cn(d.source) : ""}
          </p>
          {d.max === 0 ? (
            <p>以下是本次效果查看到的卡牌，无需选牌。查看完毕后继续。</p>
          ) : (
            <p>
              请选择 {d.min === d.max ? d.min : `${d.min}–${d.max}`} 张。
              {d.hidden
                ? "卡牌身份隐藏。"
                : "选择后，后续效果会继续提示。"}
            </p>
          )}
          {d.deckInspection && (
            <DeckBrowser
              key={d.id}
              inspection={d.deckInspection}
              names={names}
              selectedCount={selected.length}
            />
          )}
          {d.source === "Tatsugiri" && !d.hidden && (
            <p className="inspection-summary" role="status">
              {d.max === 0
                ? `这 ${d.candidates.length} 张中有 ${d.candidates.filter((c) => c.card?.trainerType === "SUPPORTER").length} 张支援者${
                    d.candidates.some(
                      (c) => c.card?.trainerType === "SUPPORTER",
                    )
                      ? `：${d.candidates
                          .filter(
                            (c) => c.card?.trainerType === "SUPPORTER",
                          )
                          .map((c) => cn(c.card?.name))
                          .join("、")}。继续后可从中选择一张。`
                      : "。继续后洗牌，不加入手牌。"
                  }`
                : "仅从刚才查看的卡牌中选择支援者，不会另外检索牌库。"}
            </p>
          )}
          {d.max === 0 ? (
            <ul className="inspection-cards" aria-label="本次查看的卡牌">
              {d.candidates.map((c, i) => (
                <li
                  key={c.ref}
                  className={
                    !d.hidden && c.card?.trainerType === "SUPPORTER"
                      ? "inspection-supporter"
                      : ""
                  }
                >
                  <strong>
                    {d.hidden ? `背面卡牌 ${i + 1}` : cn(c.card?.name)}
                  </strong>
                  {!d.hidden && (
                    <span>
                      {c.card?.trainerType === "SUPPORTER"
                        ? "支援者"
                        : c.card?.trainerType === "ITEM"
                          ? "物品"
                          : c.card?.trainerType === "TOOL"
                            ? "宝可梦道具"
                            : c.card?.superType === "ENERGY"
                              ? "能量"
                              : c.card?.superType === "POKEMON"
                                ? "宝可梦"
                                : ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <div className="decision-candidates">
              {d.candidates.map((c, i) => (
                <button
                  key={c.ref}
                  className={selected.includes(c.ref) ? "selected" : ""}
                  disabled={
                    readOnly ||
                    (!selected.includes(c.ref) &&
                      selected.length >= d.max)
                  }
                  aria-pressed={selected.includes(c.ref)}
                  onClick={() =>
                    setSelected((s) =>
                      s.includes(c.ref)
                        ? s.filter((x) => x !== c.ref)
                        : [...s, c.ref],
                    )
                  }
                >
                  {d.hidden ? (
                    c.card &&
                    c.revealedPrizeIndex !== undefined &&
                    c.revealedPrizeSide ? (
                      `明示奖赏 ${c.revealedPrizeIndex + 1} · ${cn(c.card.name)}`
                    ) : (
                      `背面卡牌 ${i + 1}`
                    )
                  ) : (
                    <>
                      {cn(c.card?.name)}
                      {c.side
                        ? c.side === "self"
                          ? " · 我方"
                          : " · 对方"
                        : ""}
                      {c.position?.endsWith("BENCH")
                        ? ` · 备战 ${c.index}`
                        : c.position?.endsWith("ACTIVE")
                          ? " · 战斗区"
                          : ""}
                      {c.card?.hp !== undefined
                        ? ` · HP ${Math.max(0, c.card.hp)}${c.card.hp <= 0 ? "（等待效果结算）" : ""}`
                        : ""}
                    </>
                  )}
                </button>
              ))}
            </div>
          )}
          <div className="decision-confirmation">
            <button
              className="primary"
              disabled={
                readOnly ||
                selected.length < d.min ||
                selected.length > d.max
              }
              onClick={() => submit({ selectedRefs: selected })}
            >
              {d.max === 0
                ? "已查看，继续结算"
                : `确认选择 ${selected.length} 张`}
            </button>
            {d.max === 0 && (
              <small>
                这些卡牌仅供查看，无需选中。确认后继续处理效果。
              </small>
            )}
            {d.min === 0 && d.max > 0 && (
              <small>允许不选，点击“确认选择 0 张”继续。</small>
            )}
          </div>
        </>
      )}
      {d?.kind === "options" &&
        d.options.length > 0 &&
        d.options.every((o) => o.actionType === "NumberOption") && (
          <p>请选择要执行的效果。</p>
        )}
      {d?.kind === "options" &&
        d.options.some((o) => o.id !== passTurn?.id) && (
          <>
            <input
              aria-label="筛选合法动作"
              placeholder="筛选卡名或动作"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <div className="decision-options">
              {d.options
                .filter(
                  (o) =>
                    o.id !== passTurn?.id &&
                    optionText(o)
                      .toLowerCase()
                      .includes(filter.toLowerCase()),
                )
                .map((o) => (
                  <button
                    key={o.id}
                    disabled={readOnly}
                    onClick={() => submit({ optionId: o.id })}
                  >
                    {optionText(o)}
                  </button>
                ))}
            </div>
          </>
        )}
      {!d && !shown.done && (
        <p>当前等待{opponentLabel}操作，轮到你时会显示可选动作。</p>
      )}
    </section>
  );
  return (
    <BattleScreen
      busy={busy}
      onRefresh={onRefresh}
      appearanceStorage={appearanceStorage}
      mobile={
        mobile
          ? {
              panelKey: `${shown.matchId}:${!!frame}:${panel}`,
              summary: (
                <div className="mobile-battle-status">
                  <h1>对战平台</h1>
                  <strong>{stepLabel}</strong>
                  <span data-ready={!!d && !readOnly} role="status">
                    {turnLabel}
                  </span>
                </div>
              ),
              activity: panel === "board" && opponentAction && (
                <button
                  className="mobile-battle-activity"
                  onClick={() => {
                    setLogRequest({
                      seq: opponentAction.seq,
                      turn: shown.observation.turn_number,
                    });
                    setPanel("log");
                  }}
                >
                  <span className="mobile-battle-activity-label">{opponentLabel}动作</span>
                  <span className="mobile-battle-activity-text" aria-live="polite">
                    {eventText(
                      opponentAction.text,
                      names,
                      opponentAction.cardGroups?.flatMap((group) => group.cards),
                    )}
                  </span>
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              ),
              menuOpen,
              onMenuChange: changeMenu,
              menu: (
                <>
                  {!frame && statusLabel}
                  {!frame && modeControls}
                  {resignPrompt || (
                    <div className="mobile-battle-menu-tools">
                      {matchActions}
                    </div>
                  )}
                </>
              ),
              footer: (
                <nav className="mobile-battle-nav" aria-label="对战视图">
                  {mobilePanels.map(([key, label, Icon]) => (
                    <button key={key} aria-pressed={panel === key} onClick={() => setPanel(key)}>
                      <Icon size={19} aria-hidden="true" />
                      <span>{label}</span>
                    </button>
                  ))}
                  {!frame && !shown.done && (
                    <button aria-haspopup="dialog" onClick={() => setActionsOpen(true)}>
                      <ListChecks size={19} aria-hidden="true" />
                      <span>
                        操作
                        {!readOnly && d?.kind === "selection" && d.max > 0 && (
                          <small>{selected.length}/{d.max}</small>
                        )}
                      </span>
                    </button>
                  )}
                </nav>
              ),
            }
          : undefined
      }
    >
      {error && (
        <div role="alert" className="error-banner">
          {error}
          {pendingCommand && onRetry && (
            <button disabled={busy} onClick={onRetry}>
              重试同一命令
            </button>
          )}
        </div>
      )}
      {mobile && pendingCommand && !busy && !error && onRetry && !frame && (
        <div className="mobile-battle-control">
          <span>上一条操作还未确认</span>
          <button className="secondary" onClick={onRetry}>
            恢复待确认操作
          </button>
        </div>
      )}
      {mobile && onAcquireControl && !shown.done && !frame && (
        <div className="mobile-battle-control">
          <span>取得操作权后可出牌</span>
          <button
            className="primary"
            disabled={busy}
            onClick={onAcquireControl}
          >
            取得操作权
          </button>
        </div>
      )}
      {!mobile && (
        <div className="battle-toolbar">
          {statusLabel}
          <strong>{stepLabel}</strong>
          <div>
            {!frame && modeControls}
            {matchActions}
          </div>
        </div>
      )}
      {!mobile && resignPrompt}
      {mobile && frame && (
        <div className="mobile-battle-replay-status">{statusLabel}</div>
      )}
      {frame && (
        <div className="battle-replay">
          <button
            aria-label="回放上一步"
            disabled={busy || replaySeq === 0}
            onClick={() => showReplay(replaySeq - 1)}
          >
            <ChevronLeft />
          </button>
          <input
            aria-label="回放步数"
            type="range"
            min={0}
            max={replayEnd}
            value={replaySeq}
            disabled={busy}
            onChange={(e) => showReplay(Number(e.target.value))}
          />
          <button
            aria-label="回放下一步"
            disabled={busy || replaySeq === replayEnd}
            onClick={() => showReplay(replaySeq + 1)}
          >
            <ChevronRight />
          </button>
          <button
            disabled={busy || !!error || replaySeq >= replayEnd}
            onClick={() => replay?.onPlayingChange(!autoplay)}
          >
            {autoplay ? "暂停回放" : "播放回放"}
          </button>
          <select
            aria-label="回放速度"
            value={speed}
            onChange={(e) => replay?.onSpeedChange(Number(e.target.value))}
          >
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={4}>4×</option>
          </select>
          <button
            disabled={busy}
            onClick={() => {
              replay?.onClose();
            }}
          >
            返回当前局面
          </button>
          <button
            disabled={busy || !previousTurn}
            onClick={() => previousTurn && showReplay(previousTurn.seq)}
          >
            上一回合
          </button>
          <select
            aria-label="回放回合"
            disabled={busy}
            value={currentTurn?.seq ?? 0}
            onChange={(e) => showReplay(Number(e.target.value))}
          >
            {timeline.map((point) => (
              <option key={point.seq} value={point.seq}>
                {point.phase === "setup"
                  ? "开局准备"
                  : `回合 ${point.turn} · ${actorName(point.player)}`}{" "}
                · 第 {point.seq} 步
              </option>
            ))}
          </select>
          <button
            disabled={busy || !nextTurn}
            onClick={() => nextTurn && showReplay(nextTurn.seq)}
          >
            下一回合
          </button>
        </div>
      )}
      {shown.done && (
        <div className="battle-result" role="status">
          {shown.status === "truncated"
            ? "达到本地步数保护上限，本局记为截断"
            : shown.result?.outcome === "draw"
              ? "平局"
              : shown.winner?.toUpperCase() === viewerSeat
                ? "你赢了！"
                : shown.winner?.toUpperCase() ===
                    (viewerSeat === "PLAYER1" ? "PLAYER2" : "PLAYER1")
                  ? shown.status === "resigned"
                    ? "你已认输"
                    : `${opponentLabel} 获胜`
                  : "对局结束"}
          <small>
            {(
              {
                prizes_taken: "已拿完全部奖赏卡",
                no_pokemon: "落败方场上已无宝可梦",
                deck_out: "回合开始时无法抽牌",
                simultaneous_draw: "双方同时满足相同数量的胜利条件，本局平局",
                simultaneous_knockout: "同时击倒结算后，胜利条件较多的一方获胜",
                resigned: "认输结束",
                technical_abort: "达到对局保护上限，技术中止，不计胜负",
                truncated: "达到步数保护上限，不计作正常胜负",
                archived:
                  "旧引擎版本已停用，仅供回放；请选择符合当前规则的卡组版本开始新对局",
              } as Record<string, string>
            )[
              shown.result?.reason || shown.observation.termination_reason || ""
            ] || "可查看逐步回放"}
            {` · 剩余奖赏：你 ${shown.observation.self.prize_count} / ${opponentLabel} ${shown.observation.opponent.prize_count}`}
          </small>
        </div>
      )}
      <div className="battle-layout">
        <div
          className="battle-table"
          hidden={mobile && panel !== "board"}
        >
          <Tabletop
            key={`${shown.matchId}:${frame ? "replay" : "live"}`}
            view={shown}
            mobile={mobile}
            animateEvents={
              (!frame || animateReplay) && (!mobile || panel === "board")
            }
            cards={names}
            onCoinPlaying={onCoinPlaying}
            opponentLabel={opponentLabel}
            viewerSeat={viewerSeat}
            locked={readOnly}
            selected={selected}
            toggle={(ref) =>
              setSelected((old) =>
                old.includes(ref)
                  ? old.filter((x) => x !== ref)
                  : old.length < (d?.max || 0)
                    ? [...old, ref]
                    : old,
              )
            }
            play={(id) => submit({ optionId: id })}
            describe={optionText}
          />
          {!mobile && publicRecords}
        </div>
        <aside
          className="battle-controls"
          hidden={mobile && panel !== "log"}
        >
          {!mobile && decisionPanel}
          <div className="battle-records" hidden={mobile && panel !== "log"}>
            {mobile && publicRecords}
            <BattleLog
              key={`${shown.matchId}:${frame ? "replay" : "live"}`}
              view={shown}
              cards={names}
              viewerSeat={viewerSeat}
              opponentLabel={opponentLabel}
              replay={!!frame}
              requestedEntry={frame ? null : logRequest}
            />
          </div>
        </aside>
      </div>
      {mobile && (
        <dialog
          ref={actionsDialog}
          className="mobile-battle-actions"
          aria-label="对局操作"
          onCancel={(event) => {
            event.preventDefault();
            setActionsOpen(false);
          }}
        >
          <header className="mobile-battle-actions-header">
            <div>
              <strong>对局操作</strong>
              <small>{stepLabel}</small>
            </div>
            <button className="secondary" onClick={() => setActionsOpen(false)}>
              收起操作
            </button>
          </header>
          {decisionPanel}
        </dialog>
      )}
    </BattleScreen>
  );
}
