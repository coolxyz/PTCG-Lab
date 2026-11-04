import { RetryImage } from "./RetryImage";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
} from "react";
import { createPortal } from "react-dom";
import type {
  BattleView,
  EngineCard,
  CardInfo,
  Option,
  Side,
  PlayerSeat,
} from "./battle/types";
import {
  eventText,
  lookupCard,
  playerLabel,
  skillName,
} from "./battle/presentation";
import "./tabletop.css";
import "./arena.css";
import { EventPlayback } from "./EventPlayback";
import { CoinResults } from "./CoinResults";
import { CardDescription } from "./CardDescription";
import { CardActionPopover } from "./battle/CardActionPopover";

const energyLabels: Record<string, string> = {
  GRASS: "草",
  FIRE: "火",
  WATER: "水",
  LIGHTNING: "雷",
  PSYCHIC: "超",
  FIGHTING: "斗",
  DARK: "恶",
  METAL: "钢",
  DRAGON: "龙",
  COLORLESS: "无色",
  FAIRY: "妖",
  ANY: "任意",
  PSYCHIC_DARK: "超／恶",
};
function energyGroups(energy: string[]) {
  const counts = new Map<string, number>();
  for (const raw of energy) {
    const type = raw.toUpperCase();
    counts.set(type, (counts.get(type) ?? 0) + 1);
  }
  return [...counts].map(([type, count]) => ({
    type,
    count,
    label: energyLabels[type] ?? type,
  }));
}
function energyText(energy: string[]) {
  return energyGroups(energy)
    .map(({ label, count }) => `${label}${count > 1 ? ` ×${count}` : ""}`)
    .join("、");
}

type Props = {
  view: BattleView;
  cards: Record<string, CardInfo>;
  locked: boolean;
  selected: string[];
  toggle: (ref: string) => void;
  play: (id: string) => void;
  describe: (o: Option) => string;
  opponentLabel?: string;
  viewerSeat?: PlayerSeat;
  animateEvents?: boolean;
  onCoinPlaying?: (playing: boolean) => void;
  mobile?: boolean;
};
export function Tabletop({
  view,
  cards,
  locked,
  selected,
  toggle,
  play,
  describe,
  opponentLabel = "对手",
  viewerSeat = "PLAYER1",
  animateEvents = true,
  onCoinPlaying,
  mobile,
}: Props) {
  const [focus, setFocus] = useState<EngineCard | null>(null);
  const [cardAnchor, setCardAnchor] = useState<{
    card: EngineCard;
    trigger: HTMLButtonElement;
    epoch: string;
  } | null>(null);
  const closeCardActions = useCallback(() => setCardAnchor(null), []);
  const [choices, setChoices] = useState<Option[] | null>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const gesture = useRef<{
    source: string;
    epoch: string;
    x: number;
    y: number;
    active: boolean;
    pointerId: number;
    element: HTMLButtonElement;
  } | null>(null);
  const suppressClick = useRef(false);
  const [sort, setSort] = useState(false);
  const [motion, setMotion] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null);
  const decision = view.decision;
  const options = decision?.kind === "options" ? decision.options : [];
  const cardInfo = (c: EngineCard) => lookupCard(c, cards);
  const name = (c: EngineCard) => cardInfo(c)?.cnName || c.name;
  const toolText = (card: EngineCard) =>
    card.tool?.map((tool) => cards[tool]?.cnName || tool).join("、");
  useEffect(() => {
    setFocus(null);
    setChoices(null);
    setDrag(null);
  }, [view.stateVersion, view.matchId]);
  useEffect(() => {
    if (focus || choices) dialog.current?.showModal();
    else dialog.current?.close();
  }, [focus, choices]);
  const actions = (card: EngineCard) =>
    options.filter(
      (o) =>
        !!card.ref && [o.sourceRef, o.active_pokemonRef].includes(card.ref),
    );
  // Drop gestures select only options already supplied by the current decision.
  // Pointer capture avoids WebView-specific HTML5/image dragging behavior.
  const epoch = `${view.matchId}:${view.stateVersion}:${decision?.id}:${locked}`;
  const activeAnchor = cardAnchor?.epoch === epoch && !locked ? cardAnchor : null;
  const currentEpoch = useRef(epoch);
  currentEpoch.current = epoch;
  const targets = (ref: string, source = drag) =>
    source && !locked ? options.filter((o) =>
      [o.sourceRef, o.active_pokemonRef].includes(source) &&
      (o.targetRef === ref || (!o.targetRef && (
        (ref === "bench" && o.actionType === "PlayPokemonAction") ||
        (ref === "stadium" && o.actionType === "PutStadiumAction") ||
        (ref === "play" && !["PlayPokemonAction", "PutStadiumAction"].includes(o.actionType))
      )))
    ) : [];
  const selectionTarget = (ref: string, source = drag) =>
    !locked && !!source && decision?.kind === "selection" &&
    decision.candidates.some((c) => c.boardRef === source) &&
    ((view.phase === "active" && ref === "active") ||
      (["bench", "bonus_bench"].includes(view.phase) && ref === "bench"));
  const ready = (ref: string) => targets(ref).length > 0 || selectionTarget(ref);
  const cancelDrag = () => {
    const g = gesture.current;
    gesture.current = null;
    if (g?.element.hasPointerCapture(g.pointerId)) g.element.releasePointerCapture(g.pointerId);
    setDrag(null);
  };
  useEffect(() => {
    cancelDrag();
    setCardAnchor(null);
  }, [epoch]);
  useEffect(() => {
    const cancel = () => cancelDrag();
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") cancelDrag(); };
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("blur", cancel);
      window.removeEventListener("keydown", escape);
    };
  }, []);
  const drop = (ref: string, source: string) => {
    if (locked) return;
    if (selectionTarget(ref, source)) {
      const c = decision?.candidates.find((c) => c.boardRef === source);
      // Setup still uses the existing explicit confirmation and selection limit.
      if (c && !selected.includes(c.ref)) toggle(c.ref);
      return;
    }
    const valid = targets(ref, source);
    if (valid.length === 1) play(valid[0].id);
    else if (valid.length) setChoices(valid);
  };
  const beginDrag = (e: PointerEvent<HTMLButtonElement>, card: EngineCard) => {
    suppressClick.current = false;
    if (mobile || e.button !== 0 || !e.isPrimary || locked || !card.ref ||
      (!actions(card).length && !candidate(card))) return;
    gesture.current = { source: card.ref, epoch, x: e.clientX, y: e.clientY,
      active: false, pointerId: e.pointerId, element: e.currentTarget };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const moveDrag = (e: PointerEvent<HTMLButtonElement>) => {
    const g = gesture.current;
    if (!g || g.pointerId !== e.pointerId) return;
    if (g.epoch !== currentEpoch.current) { cancelDrag(); return; }
    if (!g.active && Math.hypot(e.clientX - g.x, e.clientY - g.y) < 7) return;
    g.active = true;
    suppressClick.current = true;
    e.preventDefault();
    setDrag(g.source);
    setPointer({ x: e.clientX, y: e.clientY });
  };
  const endDrag = (e: PointerEvent<HTMLButtonElement>) => {
    const g = gesture.current;
    if (!g || g.pointerId !== e.pointerId) return;
    if (g.active && g.epoch === currentEpoch.current) {
      e.preventDefault();
      const hit = document.elementFromPoint(e.clientX, e.clientY);
      // Walk outwards so a card nested inside a bench/stadium zone does not
      // swallow a legal zone drop. Never infer an action from an invalid target.
      for (let node = hit; node; node = node.parentElement) {
        const ref = node.getAttribute("data-card-drop");
        if (ref && (targets(ref, g.source).length || selectionTarget(ref, g.source))) {
          drop(ref, g.source); break;
        }
      }
    }
    cancelDrag();
  };
  const candidate = (card: EngineCard) =>
    decision?.kind === "selection"
      ? decision.candidates.find((c) => c.boardRef === card.ref && !!card.ref)
      : undefined;
  const selectable = (card: EngineCard) =>
    (decision?.max ?? 0) > 0 ? candidate(card) : undefined;
  const open = (card: EngineCard, trigger: HTMLButtonElement) => {
    const c = candidate(card);
    if (mobile && !locked && (actions(card).length > 0 || selectable(card))) {
      setCardAnchor(
        activeAnchor?.trigger === trigger ? null : { card, trigger, epoch },
      );
      return;
    }
    if (!mobile && !locked && c) toggle(c.ref);
    else {
      closeCardActions();
      setFocus(card);
    }
  };
  function cardState(card: EngineCard, compact = false) {
    if (compact)
      return card.hp === undefined ? null : (
        <span className="card-state-content">
          <span className="battle-hp">HP {Math.max(0, card.hp)}</span>
        </span>
      );
    return (
      <span className="card-state-content">
        {card.hp !== undefined && (
          <span className="battle-hp">
            剩余 HP {Math.max(0, card.hp)}
            {card.maximumHp !== undefined && ` / ${card.maximumHp}`}
            {card.hp <= 0 && <small>等待本次效果结算</small>}
          </span>
        )}
        {!!card.energy?.length && (
          <span
            className="tabletop-energy"
            title={`附着能量：${energyText(card.energy)}`}
          >
            <b>能量</b>
            {energyGroups(card.energy).map(({ type, count, label }) => (
              <i key={type} data-type={type}>
                {label}
                {count > 1 && `×${count}`}
              </i>
            ))}
          </span>
        )}
        {!!card.evolved?.length && (
          <span className="tabletop-stack">进化 ×{card.evolved.length}</span>
        )}
        {!!card.tool?.length && (
          <span className="tabletop-tool" title={toolText(card)}>
            道具：{toolText(card)}
          </span>
        )}
        {card.specialCondition && (
          <span className="tabletop-condition">
            {{ ASLEEP: "睡眠", PARALYZED: "麻痹", CONFUSED: "混乱" }[
              card.specialCondition
            ] || card.specialCondition}
          </span>
        )}
        {card.retreatBlocked && (
          <span className="tabletop-condition">无法撤退</span>
        )}
        {card.attackBlocked && (
          <span className="tabletop-condition">招式使用受限</span>
        )}
        {!!card.limitedAttacks?.length && (
          <span
            className="tabletop-condition"
            title={card.limitedAttacks.join("、")}
          >
            指定招式受限
          </span>
        )}
        {card.attackProtection?.damage && (
          <span className="tabletop-condition">
            {card.attackProtection.source === "basic"
              ? "基础宝可梦伤害防护"
              : "招式伤害防护"}
          </span>
        )}
        {card.attackProtection?.effects && (
          <span className="tabletop-condition">招式效果防护</span>
        )}
        {card.abilitiesSuppressed && (
          <span className="tabletop-condition">特性已消除</span>
        )}
        {!!card.attackPowerReduction && (
          <span className="tabletop-condition">
            招式伤害 -{card.attackPowerReduction}
          </span>
        )}
        {!!card.attackPowerBonus && (
          <span className="tabletop-condition">
            招式伤害 +{card.attackPowerBonus}
          </span>
        )}
        {!!card.retaliationCounters && (
          <span className="tabletop-condition">
            受击反击 {card.retaliationCounters / 10} 个指示物
          </span>
        )}
        {card.poisoned && (
          <span className="tabletop-condition">
            {(card.poisonDamage || 10) > 10
              ? `中毒 · 检查伤害 ${card.poisonDamage}`
              : "中毒"}
          </span>
        )}
        {card.burned && <span className="tabletop-condition">灼伤</span>}
        {!!card.attackDamageReduction && (
          <span className="tabletop-condition">
            招式伤害 −{card.attackDamageReduction}
          </span>
        )}
        {card.preventAttackDamageAtMost !== undefined && (
          <span className="tabletop-condition">
            防止 {card.preventAttackDamageAtMost} 及以下的招式伤害
          </span>
        )}
        {card.abilityUsed && <span className="tabletop-used">特性已用</span>}
      </span>
    );
  }
  function picture(
    card: EngineCard,
    key: number | string,
    zone?: "hand" | "bench",
    motionLocation?: string,
  ) {
    const location = motionLocation ?? card.ref?.split(":").slice(1).join(":");
    const info = cardInfo(card);
    const c = candidate(card);
    const canUse =
      !locked && (actions(card).length > 0 || !!(mobile ? selectable(card) : c));
    const expanded =
      mobile && canUse ? activeAnchor?.card.ref === card.ref : undefined;
    const benchEnergy = mobile && zone === "bench" ? card.energy ?? [] : [];
    const benchTypes = energyGroups(benchEnergy);
    const benchToolCount =
      mobile && zone === "bench" ? card.tool?.length ?? 0 : 0;
    const attachmentLabel = [
      benchEnergy.length ? `附着能量 ${energyText(benchEnergy)}` : "",
      benchToolCount ? `道具 ${toolText(card)}` : "",
    ].filter(Boolean).join(" · ");
    return (
      <div
        key={key}
        data-motion-card={location}
        data-motion-name={card.name}
        className={`tabletop-slot ${ready(card.ref || "") ? "drop-ready" : ""}`}
        data-card-drop={card.ref}
      >
        <button
          className={`tabletop-card ${canUse ? "usable" : ""} ${c && selected.includes(c.ref) ? "chosen" : ""}`}
          aria-label={`${name(card)}${card.hp !== undefined ? ` · HP ${Math.max(0, card.hp)}` : ""}${attachmentLabel ? ` · ${attachmentLabel}` : ""}`}
          aria-pressed={!mobile && c ? selected.includes(c.ref) : undefined}
          aria-expanded={expanded}
          aria-haspopup={mobile && !canUse ? "dialog" : undefined}
          draggable={false}
          onPointerDown={(e) => beginDrag(e, card)}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={cancelDrag}
          onLostPointerCapture={cancelDrag}
          onClick={(event) => {
            if (suppressClick.current) {
              suppressClick.current = false;
              return;
            }
            open(card, event.currentTarget);
          }}
        >
          {info?.image.url && (
            <RetryImage
              src={info.image.url}
              draggable={false}
              alt=""
              loading="lazy"
            />
          )}
          <span className="card-fallback">{name(card)}</span>
          {attachmentLabel && (
            <span className="mobile-bench-attachments" aria-hidden="true">
              {!!benchEnergy.length && (
                <span>
                  {benchTypes.length === 1 && benchTypes[0].label.length === 1
                    ? benchTypes[0].label
                    : "能"}
                  {benchEnergy.length}
                </span>
              )}
              {!!benchToolCount && <span className="mobile-bench-tool">具{benchToolCount}</span>}
            </span>
          )}
          {mobile && zone === "bench" && card.hp !== undefined && (
            <span className="mobile-bench-hp" aria-hidden="true">
              HP {Math.max(0, card.hp)}
            </span>
          )}
          {card.hp !== undefined && !!card.maximumHp && (
            <span className="arena-health" aria-hidden="true">
              <i
                style={{
                  width: `${Math.min(100, Math.max(0, (card.hp / card.maximumHp) * 100))}%`,
                }}
              />
            </span>
          )}
        </button>
        {mobile && zone === "hand" && (
          <span className="mobile-hand-caption" aria-hidden="true">
            {name(card)}
          </span>
        )}
        {(card.hp !== undefined ||
          !!card.tool?.length ||
          !!card.energy?.length) && (
          <button
            className="card-state-panel"
            aria-label={`查看状态：${name(card)}`}
            onClick={() => setFocus(card)}
          >
            <strong className="card-state-name">{name(card)}</strong>
            {cardState(card, !!mobile && zone === "bench")}
            <span className="card-state-more">状态与效果 ›</span>
          </button>
        )}
      </div>
    );
  }
  function prizes(side: Side, enemy: boolean) {
    return (
      <div
        className="tabletop-prizes"
        data-motion-zone={`${enemy ? "opponent" : "self"}:prize`}
      >
        <small>奖赏 · {side.prize_count}</small>
        <div>
          {Array.from({ length: side.prize_count }, (_, i) => {
            const visible = side.faceUpPrizes?.find((c) => c.prizeIndex === i);
            return visible ? (
              <button
                key={i}
                className="face-up-prize"
                aria-label={`正面奖赏 ${name(visible)}`}
                onClick={(event) => open(visible, event.currentTarget)}
              >
                {name(visible)}
              </button>
            ) : !mobile ? (
              <i key={i} className="cardback" aria-label="背面奖赏" />
            ) : null;
          })}
        </div>
      </div>
    );
  }
  function piles(side: Side, enemy: boolean) {
    return (
      <div className="tabletop-piles">
        {!mobile && (
          <div
            className="deck-pile cardback"
            data-motion-zone={`${enemy ? "opponent" : "self"}:deck`}
          >
            <b>{side.deck_count}</b>
            <small>牌库</small>
          </div>
        )}
        {(
          [
            ["弃牌", "discard", side.discard],
            ["放逐", "lost_zone", side.lost_zone],
          ] as const
        ).map(([label, zone, contents]) => (
          <details
            key={zone}
            data-motion-zone={`${enemy ? "opponent" : "self"}:${zone}`}
          >
            <summary>
              {label} {contents.length}
            </summary>
            <div className="pile-cards">
              {contents.length || !mobile
                ? contents.map((card, i) => picture(card, i))
                : "暂无卡牌"}
            </div>
          </details>
        ))}
      </div>
    );
  }
  function side(side: Side, enemy: boolean) {
    const player = enemy ? "opponent" : "self";
    return (
      <section
        className={`tabletop-player ${enemy ? "enemy" : "own"}`}
        aria-label={enemy ? `${opponentLabel} 场面` : "我的场面"}
      >
        <header>
          <strong className="arena-trainer">
            <i aria-hidden="true">{enemy ? opponentLabel : "你"}</i>
            <span>
              {enemy ? opponentLabel : "我的场面"}
              {!mobile && <small>{enemy ? "OPPONENT" : "TRAINER"}</small>}
            </span>
          </strong>
          <span className="tabletop-counts">
            <span
              data-motion-zone={
                mobile ? `${enemy ? "opponent" : "self"}:deck` : undefined
              }
            >
              牌库 {side.deck_count}
            </span>
            {" · "}
            <span
              data-motion-zone={
                mobile && enemy ? "opponent:hand" : undefined
              }
            >
              手牌 {side.hand_count}
            </span>
            {" · "}
            <strong data-motion-zone={mobile ? `${enemy ? "opponent" : "self"}:prize` : undefined}>
              奖赏 {side.prize_count}
            </strong>
          </span>
          {side.turnFlags?.itemsBlocked && (
            <span className="tabletop-condition">物品使用受限</span>
          )}
          {side.turnFlags?.attacksBlocked && (
            <span className="tabletop-condition">本回合无法使用招式</span>
          )}
        </header>
        {enemy && !mobile && (
          <div
            className="opponent-hand"
            data-motion-zone="opponent:hand"
            aria-label={`对手手牌 ${side.hand_count} 张`}
          >
            {Array.from({ length: Math.min(12, side.hand_count) }, (_, i) => (
              <i key={i} className="cardback" />
            ))}
            <span>{side.hand_count} 张</span>
          </div>
        )}
        <div className="tabletop-field">
          {!mobile && prizes(side, enemy)}
          <div className="tabletop-center">
            <div
              className={`tabletop-active ${!enemy && ready("active") ? "drop-ready" : ""}`}
              data-card-drop={enemy ? undefined : "active"}
              data-motion-zone={`${enemy ? "opponent" : "self"}:active`}
            >
              <small>战斗区</small>
              <div>
                {side.active.length ? (
                  side.active.map((card, i) =>
                    picture(card, i, undefined, `${player}:active:${i}`),
                  )
                ) : (
                  <span className="empty-slot">战斗宝可梦</span>
                )}
              </div>
            </div>
            <div
              className={`tabletop-bench ${!enemy && ready("bench") ? "drop-ready" : ""}`}
              data-card-drop={enemy ? undefined : "bench"}
              data-motion-zone={`${enemy ? "opponent" : "self"}:bench`}
            >
              <small>
                备战区 · {side.bench.length}/{side.benchCapacity || 5}
              </small>
              <div>
                {side.bench.map((card, i) =>
                  picture(card, i, "bench", `${player}:bench:${i}`),
                )}
                {mobile && !side.bench.length && (
                  <span className="mobile-empty-bench">暂无备战宝可梦</span>
                )}
                {!mobile &&
                  Array.from(
                    {
                      length: Math.max(
                        0,
                        (side.benchCapacity || 5) - side.bench.length,
                      ),
                    },
                    (_, i) => (
                      <span className="empty-slot" key={`empty-${i}`}>
                        ＋
                      </span>
                    ),
                  )}
              </div>
            </div>
          </div>
          {!mobile && piles(side, enemy)}
        </div>
      </section>
    );
  }
  const hand = [...(view.observation.self.hand || [])];
  if (sort) hand.sort((a, b) => name(a).localeCompare(name(b), "zh-CN"));
  const shownActions = choices || (focus ? actions(focus) : []);
  const anchoredCandidate = activeAnchor && selectable(activeAnchor.card);
  const lastAction = view.events?.at(-1);
  const playback = (
    <EventPlayback
      view={view}
      cards={cards}
      enabled={motion && animateEvents}
      opponentLabel={opponentLabel}
      viewerSeat={viewerSeat}
      mobile={mobile}
    />
  );
  const settings = (
    <div className="tabletop-settings">
      <span>
        {mobile ? "点按卡牌查看状态或操作" : "点击卡牌操作 · 拖到高亮目标"}
      </span>
      <div className="tabletop-animation-controls">
        {!mobile && playback}
        <button onClick={() => setMotion(!motion)}>
          {motion ? "关闭动画" : "开启动画"}
        </button>
      </div>
    </div>
  );
  const cardActions = (
    <div className="tabletop-actions">
      {shownActions.map((o) => (
        <button
          key={o.id}
          disabled={locked}
          onClick={() => {
            setFocus(null);
            setChoices(null);
            play(o.id);
          }}
        >
          {describe(o)}
        </button>
      ))}
      {!shownActions.length && !mobile && (
        <p>当前没有可执行动作，可查看卡牌信息。</p>
      )}
    </div>
  );
  return (
    <div
      className={`tabletop arena ${motion && animateEvents ? "with-motion" : ""}`}
    >
      {!mobile && (
        <div className="arena-scoreboard">
          <span>
            <small>对手剩余奖赏</small>
            <b>{view.observation.opponent.prize_count}</b>
          </span>
          <div>
            <small>回合 {view.observation.turn_number}</small>
            <strong role="status">
              {view.done
                ? "对局结束"
                : decision
                  ? "等待你的操作"
                  : "对手行动中"}
            </strong>
          </div>
          <span>
            <small>我的剩余奖赏</small>
            <b>{view.observation.self.prize_count}</b>
          </span>
        </div>
      )}
      {!mobile && settings}
      {side(view.observation.opponent, true)}
      {mobile && playback}
      <div
        className={`tabletop-stadium ${ready("stadium") ? "drop-ready" : ""}`}
        data-card-drop="stadium"
      >
        <span className="arena-stadium-label">竞技场</span>
        {view.observation.stadium.length ? (
          mobile ? (
            view.observation.stadium.map((card, i) => (
              <button
                key={card.ref || i}
                className="secondary"
                aria-expanded={
                  !locked && actions(card).length
                    ? activeAnchor?.card.ref === card.ref
                    : undefined
                }
                onClick={(event) => open(card, event.currentTarget)}
              >
                {name(card)}
                {!locked && actions(card).length > 0 ? " · 可使用" : ""}
              </button>
            ))
          ) : (
            view.observation.stadium.map((card, i) => picture(card, i))
          )
        ) : (
          <small>尚未放置</small>
        )}
        {!mobile && (
          <strong className="arena-versus" aria-hidden="true">
            VS
          </strong>
        )}
      </div>
      {mobile && side(view.observation.self, false)}
      {lastAction && (
        <section
          className={`arena-action-banner ${playerLabel(lastAction.actor, viewerSeat, opponentLabel) === opponentLabel ? "is-ai" : ""}`}
          aria-label="牌桌当前动作"
          aria-live="polite"
        >
          <span className="arena-action-actor">
            {playerLabel(lastAction.actor, viewerSeat, opponentLabel)}
            <small>第 {lastAction.seq} 步</small>
          </span>
          <div>
            <strong>
              {eventText(
                lastAction.text,
                cards,
                lastAction.cardGroups?.flatMap((g) => g.cards),
              )}
            </strong>
            <div className="arena-action-effects">
              {lastAction.effects
                ?.filter(
                  (e) =>
                    e.kind === "hp" || e.kind === "count" || e.kind === "move",
                )
                .map((e, i) => {
                  const zone =
                    (
                      {
                        hand: "手牌",
                        deck: "牌库",
                        prize: "奖赏",
                        active: "战斗区",
                        bench: "备战区",
                        discard: "弃牌区",
                        lost_zone: "放逐区",
                      } as Record<string, string>
                    )[e.zone] || e.zone;
                  return (
                    <span key={i}>
                      {e.side === "self" ? "你" : opponentLabel} ·{" "}
                      {e.kind === "move"
                        ? `${e.cardName ? cards[e.cardName]?.cnName || e.cardName : "背面卡牌"} → ${zone}`
                        : `${zone}${e.index !== undefined ? ` ${e.index + 1}` : ""}${e.kind === "hp" ? " HP" : ""} ${e.before} → ${e.after}`}
                    </span>
                  );
                })}
            </div>
          </div>
        </section>
      )}
      {!!view.visualEffects?.length && !lastAction?.effects?.length && (
        <section className="arena-action-banner" aria-label="局面变化">
          <strong>局面变化</strong>
          <div className="arena-action-effects">
            {view.visualEffects.map((e, i) => (
              <span key={i}>
                {e.side === "self" ? "你" : opponentLabel} ·{" "}
                {(
                  {
                    active: "战斗区",
                    bench: "备战区",
                    hand: "手牌",
                    deck: "牌库",
                    prize: "奖赏",
                    discard: "弃牌区",
                    lostZone: "放逐区",
                    lost_zone: "放逐区",
                  } as Record<string, string>
                )[e.zone] || e.zone}
                {e.index === undefined ? "" : ` ${e.index + 1}`} ·{" "}
                {e.kind === "hp" ? "HP " : ""}
                {e.before} → {e.after}
                {e.cardName
                  ? ` · ${cards[e.cardName]?.cnName || e.cardName}`
                  : ""}
              </span>
            ))}
          </div>
        </section>
      )}
      {!mobile && side(view.observation.self, false)}
      {!mobile && (
        <div
          className={`tabletop-play-zone ${ready("play") ? "drop-ready" : ""}`}
          data-card-drop="play"
        >
          <strong>打出区</strong>
          <span>物品、支援者或特性可拖到这里；有多个操作时再选择</span>
        </div>
      )}
      {mobile && (
        <details className="mobile-battle-zones">
          <summary>查看奖赏、弃牌与放逐区</summary>
          {(
            [
              ["我方", view.observation.self, false],
              [opponentLabel, view.observation.opponent, true],
            ] as const
          ).map(([label, player, enemy]) => (
            <section key={String(enemy)} aria-label={`${label}其他区域`}>
              <strong>{label}</strong>
              {prizes(player, enemy)}
              {piles(player, enemy)}
            </section>
          ))}
        </details>
      )}
      {mobile && settings}
      {drag &&
        createPortal(
          <div
            className="tabletop-drag-preview"
            style={{ left: pointer.x + 16, top: pointer.y + 16 }}
            aria-hidden="true"
          >
            {(() => {
              const card = [
                ...(view.observation.self.hand || []),
                ...view.observation.self.active,
                ...view.observation.self.bench,
                ...view.observation.stadium,
              ].find((c) => c.ref === drag);
              const image = card && cardInfo(card)?.image.url;
              return (
                <>
                  {image && <RetryImage src={image} alt="" draggable={false} />}
                  <span>{card ? name(card) : "卡牌"}</span>
                </>
              );
            })()}
          </div>,
          document.body,
        )}
      <CoinResults
        key={`coins:${view.matchId}:${lastAction?.seq}`}
        coins={lastAction?.coins || []}
        animate={motion && animateEvents}
        onPlaying={onCoinPlaying}
      />
      <section
        className="tabletop-hand"
        aria-label="我的手牌"
        data-motion-zone="self:hand"
      >
        <header>
          <strong>我的手牌 · {hand.length}</strong>
          <button aria-pressed={sort} onClick={() => setSort(!sort)}>
            {sort ? "恢复手牌顺序" : "按名称整理"}
          </button>
        </header>
        {mobile && !hand.length ? (
          <p>当前没有可显示的手牌。</p>
        ) : (
          <div>{hand.map((card, i) => picture(card, i, "hand"))}</div>
        )}
      </section>
      {!mobile && (
        <div
          className="tabletop-feedback"
          key={`dialog:${view.matchId}:${view.stateVersion}`}
          aria-live="polite"
        >
          {view.events?.at(-1)?.text ||
            (decision ? "轮到你选择" : `等待${opponentLabel}操作`)}
        </div>
      )}
      <dialog
        ref={dialog}
        className="tabletop-dialog"
        aria-label={
          mobile ? (focus ? name(focus) : "选择卡牌操作") : undefined
        }
        onCancel={() => {
          setFocus(null);
          setChoices(null);
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget) {
            setFocus(null);
            setChoices(null);
          }
        }}
      >
        <button
          className="dialog-close"
          aria-label="关闭卡牌"
          onClick={() => {
            setFocus(null);
            setChoices(null);
          }}
        >
          ×
        </button>
        {focus && (
          <div className="tabletop-detail">
            <div>
              {cardInfo(focus)?.image.url ? (
                <RetryImage
                  src={cardInfo(focus)?.image.url || undefined}
                  alt={name(focus)}
                />
              ) : (
                <div className="detail-placeholder">{name(focus)}</div>
              )}
              <small>{cardInfo(focus)?.image.label || "暂无对应卡图"}</small>
            </div>
            <div>
              <h2>{name(focus)}</h2>
              <section className="card-state-detail" aria-label="卡牌当前状态">
                <h3>当前状态</h3>
                {focus.retreatCost !== undefined && (
                  <p className="tabletop-retreat-cost">
                    撤退所需能量：
                    {focus.retreatCost.length
                      ? energyText(focus.retreatCost)
                      : "无需能量"}
                  </p>
                )}
                {cardState(focus)}
              </section>
              <p>{cardInfo(focus)?.reviewNote}</p>
              {!!cardInfo(focus)?.chineseDetails?.sections?.length && (
                <CardDescription
                  details={cardInfo(focus)?.chineseDetails}
                  types={energyLabels}
                />
              )}
              {!cardInfo(focus)?.chineseDetails?.sections?.length &&
                focus.attacks?.map((a) => (
                  <p key={a.name}>
                    <strong>
                      {skillName(a.name, "attack", focus, cardInfo(focus))} ·{" "}
                      {a.damage || "效果"}
                    </strong>
                    <br />
                    {a.text}
                  </p>
                ))}
              {!!focus.evolved?.length && (
                <p>
                  进化链：
                  {focus.evolved.map((n) => cards[n]?.cnName || n).join(" → ")}
                </p>
              )}
              {!cardInfo(focus)?.chineseDetails?.sections?.length &&
                focus.abilities?.map((a) => (
                  <p key={a.name}>
                    <strong>{a.name}</strong>
                    <br />
                    {a.text}
                  </p>
                ))}
            </div>
          </div>
        )}
        {!mobile && cardActions}
      </dialog>
      {activeAnchor && (
        <CardActionPopover
          trigger={activeAnchor.trigger}
          name={name(activeAnchor.card)}
          onClose={closeCardActions}
        >
          {actions(activeAnchor.card).map((option) => (
            <button
              key={option.id}
              onClick={() => {
                activeAnchor.trigger.focus({ preventScroll: true });
                closeCardActions();
                play(option.id);
              }}
            >
              {describe(option)}
            </button>
          ))}
          {anchoredCandidate && (
            <button
              disabled={
                !selected.includes(anchoredCandidate.ref) &&
                selected.length >= (decision?.max ?? 0)
              }
              onClick={() => {
                toggle(anchoredCandidate.ref);
                activeAnchor.trigger.focus({ preventScroll: true });
                closeCardActions();
              }}
            >
              {selected.includes(anchoredCandidate.ref)
                ? "取消选择此卡"
                : "选择此卡"}
            </button>
          )}
          <button
            className="secondary"
            aria-haspopup="dialog"
            onClick={() => {
              activeAnchor.trigger.focus({ preventScroll: true });
              setFocus(activeAnchor.card);
              closeCardActions();
            }}
          >
            查看卡牌详情
          </button>
        </CardActionPopover>
      )}
    </div>
  );
}
