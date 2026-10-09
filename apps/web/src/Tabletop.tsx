import { useEffect, useRef, useState } from "react";
import type { BattleView, EngineCard, CardInfo, Option, Side } from "./Battle";
import "./tabletop.css";
import "./arena.css";
import { EventPlayback } from "./EventPlayback";
import { CoinResults } from "./CoinResults";
import { CardDescription } from "./CardArtwork";

type Props = {
  view: BattleView;
  cards: Record<string, CardInfo>;
  locked: boolean;
  selected: string[];
  toggle: (ref: string) => void;
  play: (id: string) => void;
  describe: (o: Option) => string;
  animateEvents?: boolean;
  onCoinPlaying?: (playing: boolean) => void;
};
export function Tabletop({
  view,
  cards,
  locked,
  selected,
  toggle,
  play,
  describe,
  animateEvents = true,
  onCoinPlaying,
}: Props) {
  const [focus, setFocus] = useState<EngineCard | null>(null);
  const [choices, setChoices] = useState<Option[] | null>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const [sort, setSort] = useState(false);
  const [motion, setMotion] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null);
  const decision = view.decision;
  const options = decision?.kind === "options" ? decision.options : [];
  const cardInfo = (c: EngineCard) => (c.id && cards[c.id]) || cards[c.name];
  const name = (c: EngineCard) => cardInfo(c)?.cnName || c.name;
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
  const targets = (ref: string) =>
    options.filter(
      (o) =>
        o.sourceRef === drag &&
        (o.targetRef === ref ||
          (ref === "bench" && o.actionType === "PlayPokemonAction")),
    );
  const drop = (ref: string) => {
    if (locked) return;
    const valid = targets(ref);
    setDrag(null);
    if (valid.length === 1) play(valid[0].id);
    else if (valid.length) setChoices(valid);
  };
  const candidate = (card: EngineCard) =>
    decision?.kind === "selection"
      ? decision.candidates.find((c) => c.boardRef === card.ref && !!card.ref)
      : undefined;
  const open = (card: EngineCard) => {
    const c = candidate(card);
    if (!locked && c) toggle(c.ref);
    else setFocus(card);
  };
  function cardState(card: EngineCard) {
    return <span className="card-state-content">
          {card.hp !== undefined && (
            <span className="battle-hp">
              剩余 HP {Math.max(0, card.hp)}
              {card.maximumHp !== undefined && ` / ${card.maximumHp}`}
              {card.hp <= 0 && <small>等待本次效果结算</small>}
            </span>
          )}
          {!!card.energy?.length && (
            <span className="tabletop-energy" title={card.energy.join(" / ")}>
              {card.energy.map((e, i) => (
                <i key={i} data-type={e}>
                  {(
                    {
                      FIRE: "火",
                      METAL: "钢",
                      PSYCHIC: "超",
                      ANY: "★",
                      COLORLESS: "无",
                      DARK: "恶",
                    } as Record<string, string>
                  )[e] || e.slice(0, 1)}
                </i>
              ))}
            </span>
          )}
          {!!card.evolved?.length && (
            <span className="tabletop-stack">进化 ×{card.evolved.length}</span>
          )}
          {!!card.tool?.length && (
            <span className="tabletop-tool">道具：{card.tool.map(n => cards[n]?.cnName || n).join("、")}</span>
          )}
          {card.specialCondition && (
            <span className="tabletop-condition">{{ASLEEP: "睡眠", PARALYZED: "麻痹", CONFUSED: "混乱"}[card.specialCondition] || card.specialCondition}</span>
          )}
          {card.retreatBlocked && (
            <span className="tabletop-condition">无法撤退</span>
          )}
          {card.attackBlocked && (
            <span className="tabletop-condition">招式使用受限</span>
          )}
          {!!card.limitedAttacks?.length && <span className="tabletop-condition" title={card.limitedAttacks.join("、")}>指定招式受限</span>}
          {card.attackProtection?.damage && <span className="tabletop-condition">{card.attackProtection.source === "basic" ? "基础宝可梦伤害防护" : "招式伤害防护"}</span>}
          {card.attackProtection?.effects && <span className="tabletop-condition">招式效果防护</span>}
          {card.abilitiesSuppressed && <span className="tabletop-condition">特性已消除</span>}
          {!!card.attackPowerReduction && <span className="tabletop-condition">招式伤害 -{card.attackPowerReduction}</span>}
          {!!card.attackPowerBonus && <span className="tabletop-condition">招式伤害 +{card.attackPowerBonus}</span>}
          {!!card.retaliationCounters && <span className="tabletop-condition">受击反击 {card.retaliationCounters / 10} 个指示物</span>}
          {card.poisoned && <span className="tabletop-condition">{(card.poisonDamage || 10) > 10 ? `中毒 · 检查伤害 ${card.poisonDamage}` : "中毒"}</span>}
          {card.burned && <span className="tabletop-condition">灼伤</span>}
          {!!card.attackDamageReduction && (
            <span className="tabletop-condition">招式伤害 −{card.attackDamageReduction}</span>
          )}
          {card.preventAttackDamageAtMost !== undefined && (
            <span className="tabletop-condition">防止 {card.preventAttackDamageAtMost} 及以下的招式伤害</span>
          )}
          {card.abilityUsed && <span className="tabletop-used">特性已用</span>}
    </span>;
  }
  function picture(card: EngineCard, key: number | string) {
    const location = card.ref?.split(":").slice(1).join(":");
    const info = cardInfo(card);
    const c = candidate(card);
    const canUse = !locked && (actions(card).length > 0 || !!c);
    return (
      <div
        key={key}
        data-motion-card={location}
        className={`tabletop-slot ${targets(card.ref || "").length ? "drop-ready" : ""}`}
        onDragOver={(e) => {
          if (targets(card.ref || "").length) e.preventDefault();
        }}
        onDrop={(e) => {
          e.preventDefault();
          drop(card.ref || "");
        }}
      >
        <button
          className={`tabletop-card ${canUse ? "usable" : ""} ${c && selected.includes(c.ref) ? "chosen" : ""}`}
          aria-label={`${name(card)}${card.hp !== undefined ? ` · HP ${Math.max(0, card.hp)}` : ""}`}
          draggable={!locked && actions(card).length > 0}
          onDragStart={(e) => {
            setDrag(card.ref || null);
            e.dataTransfer.setData("text/plain", card.ref || "");
          }}
          onDragEnd={() => setDrag(null)}
          onClick={() => open(card)}
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
          <span className="card-fallback">{name(card)}</span>
          {card.hp !== undefined && !!card.maximumHp && (
            <span className="arena-health" aria-hidden="true"><i style={{ width: `${Math.min(100, Math.max(0, card.hp / card.maximumHp * 100))}%` }} /></span>
          )}

        </button>
        {(card.hp !== undefined || !!card.tool?.length || !!card.energy?.length) && (
          <button className="card-state-panel" aria-label={`查看状态：${name(card)}`}
            onClick={() => setFocus(card)}>
            <strong className="card-state-name">{name(card)}</strong>
            {cardState(card)}
            <span className="card-state-more">状态与效果 ›</span>
          </button>
        )}
      </div>
    );
  }
  function side(side: Side, enemy: boolean) {
    return (
      <section
        className={`tabletop-player ${enemy ? "enemy" : "own"}`}
        aria-label={enemy ? "AI 场面" : "我的场面"}
      >
        <header>
          <strong className="arena-trainer"><i aria-hidden="true">{enemy ? "AI" : "你"}</i><span>{enemy ? "AI 对手" : "我的场面"}<small>{enemy ? "OPPONENT" : "TRAINER"}</small></span></strong>
          {side.turnFlags?.itemsBlocked && <span className="tabletop-condition">物品使用受限</span>}
          {side.turnFlags?.attacksBlocked && <span className="tabletop-condition">本回合无法使用招式</span>}
          <span>
            牌库 {side.deck_count} · 手牌 {side.hand_count} · 奖赏{" "}
            {side.prize_count}
          </span>
        </header>
        {enemy && (
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
          <div
            className="tabletop-prizes"
            data-motion-zone={`${enemy ? "opponent" : "self"}:prize`}
          >
            <small>奖赏 · {side.prize_count}</small>
            <div>
                {Array.from({ length: side.prize_count }, (_, i) => {
                  const visible = side.faceUpPrizes?.find(c => c.prizeIndex === i);
                  return visible ? (
                    <button key={i} className="face-up-prize" aria-label={`正面奖赏 ${name(visible)}`} onClick={() => open(visible)}>
                      {name(visible)}
                    </button>
                  ) : <i key={i} className="cardback" aria-label="背面奖赏" />;
                })}
            </div>
          </div>
          <div className="tabletop-center">
            <div
              className="tabletop-active"
              data-motion-zone={`${enemy ? "opponent" : "self"}:active`}
            >
              <small>战斗区</small>
              <div>
                {side.active.length ? (
                  side.active.map(picture)
                ) : (
                  <span className="empty-slot">战斗宝可梦</span>
                )}
              </div>
            </div>
            <div
              className={`tabletop-bench ${!enemy && targets("bench").length ? "drop-ready" : ""}`}
              data-motion-zone={`${enemy ? "opponent" : "self"}:bench`}
              onDragOver={(e) => {
                if (!enemy && targets("bench").length) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (!enemy) drop("bench");
              }}
            >
              <small>
                备战区 · {side.bench.length}/{side.benchCapacity || 5}
              </small>
              <div>
                {side.bench.map(picture)}
                {Array.from(
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
          <div className="tabletop-piles">
            <div
              className="deck-pile cardback"
              data-motion-zone={`${enemy ? "opponent" : "self"}:deck`}
            >
              <b>{side.deck_count}</b>
              <small>牌库</small>
            </div>
            <details
              data-motion-zone={`${enemy ? "opponent" : "self"}:discard`}
            >
              <summary>弃牌 {side.discard.length}</summary>
              <div className="pile-cards">{side.discard.map(picture)}</div>
            </details>
            <details
              data-motion-zone={`${enemy ? "opponent" : "self"}:lost_zone`}
            >
              <summary>放逐 {side.lost_zone.length}</summary>
              <div className="pile-cards">{side.lost_zone.map(picture)}</div>
            </details>
          </div>
        </div>
      </section>
    );
  }
  const hand = [...(view.observation.self.hand || [])];
  if (sort) hand.sort((a, b) => name(a).localeCompare(name(b), "zh-CN"));
  const shownActions = choices || (focus ? actions(focus) : []);
  const lastAction = view.events?.at(-1);
  return (
    <div className={`tabletop arena ${motion && animateEvents ? "with-motion" : ""}`}>
      <div className="arena-scoreboard">
        <span><small>对手剩余奖赏</small><b>{view.observation.opponent.prize_count}</b></span>
        <div><small>回合 {view.observation.turn_number}</small><strong role="status">{view.done ? "对局结束" : decision ? "等待你的操作" : "对手行动中"}</strong></div>
        <span><small>我的剩余奖赏</small><b>{view.observation.self.prize_count}</b></span>
      </div>
      <div className="tabletop-settings">
        <span>点击卡牌操作 · 拖到高亮目标</span>
        <button onClick={() => setMotion(!motion)}>
          {motion ? "关闭动画" : "开启动画"}
        </button>
      </div>
      {side(view.observation.opponent, true)}
      <EventPlayback
        view={view}
        cards={cards}
        enabled={motion && animateEvents}
      />
      <div className="tabletop-stadium">
        <span className="arena-stadium-label">竞技场</span>
        {view.observation.stadium.length ? (
          view.observation.stadium.map(picture)
        ) : (
          <small>尚未放置</small>
        )}
        <strong className="arena-versus" aria-hidden="true">VS</strong>
      </div>
      {lastAction && <section className={`arena-action-banner ${lastAction.actor === "ai" ? "is-ai" : ""}`} aria-label="牌桌当前动作" aria-live="polite">
        <span className="arena-action-actor">{lastAction.actor === "ai" ? "AI 对手" : lastAction.actor === "you" ? "你" : "系统"}<small>第 {lastAction.seq} 步</small></span>
        <div><strong>{lastAction.text}</strong>
          <div className="arena-action-effects">{lastAction.effects?.filter(e => e.kind === "hp" || e.kind === "count" || e.kind === "move").map((e, i) => {
            const zone = ({hand:"手牌",deck:"牌库",prize:"奖赏",active:"战斗区",bench:"备战区",discard:"弃牌区",lost_zone:"放逐区"} as Record<string,string>)[e.zone] || e.zone;
            return <span key={i}>{e.side === "self" ? "你" : "AI"} · {e.kind === "move" ? `${e.cardName ? cards[e.cardName]?.cnName || e.cardName : "背面卡牌"} → ${zone}` : `${zone}${e.index !== undefined ? ` ${e.index + 1}` : ""}${e.kind === "hp" ? " HP" : ""} ${e.before} → ${e.after}`}</span>;
          })}</div>
        </div>
      </section>}
      {side(view.observation.self, false)}
      <CoinResults key={`${view.matchId}:${lastAction?.seq}`} coins={lastAction?.coins || []} animate={motion && animateEvents} onPlaying={onCoinPlaying} />
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
        <div>{hand.map(picture)}</div>
      </section>
      <div
        className="tabletop-feedback"
        key={`${view.matchId}:${view.stateVersion}`}
        aria-live="polite"
      >
        {view.events?.at(-1)?.text ||
          (decision ? "轮到你选择" : "等待 AI 操作")}
      </div>
      <dialog
        ref={dialog}
        className="tabletop-dialog"
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
                <img src={cardInfo(focus).image.url!} alt={name(focus)} />
              ) : (
                <div className="detail-placeholder">{name(focus)}</div>
              )}
              <small>{cardInfo(focus)?.image.label || "暂无对应卡图"}</small>
            </div>
            <div>
              <h2>{name(focus)}</h2>
              <section className="card-state-detail" aria-label="卡牌当前状态"><h3>当前状态</h3>{cardState(focus)}</section>
              <p>{cardInfo(focus)?.reviewNote}</p>
              {!!cardInfo(focus)?.chineseDetails?.sections?.length && <CardDescription details={cardInfo(focus).chineseDetails} types={{ GRASS: "草", FIRE: "火", WATER: "水", LIGHTNING: "雷", PSYCHIC: "超", FIGHTING: "斗", DARK: "恶", METAL: "钢", DRAGON: "龙", COLORLESS: "无", FAIRY: "妖" }} />}
              {!cardInfo(focus)?.chineseDetails?.sections?.length && focus.attacks?.map((a) => (
                <p key={a.name}>
                  <strong>
                    {a.name} · {a.damage || "效果"}
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
              {!!focus.tool?.length && (
                <p>
                  道具：
                  {focus.tool.map((n) => cards[n]?.cnName || n).join("、")}
                </p>
              )}
              {!cardInfo(focus)?.chineseDetails?.sections?.length && focus.abilities?.map((a) => (
                <p key={a.name}>
                  <strong>{a.name}</strong>
                  <br />
                  {a.text}
                </p>
              ))}
            </div>
          </div>
        )}
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
          {!shownActions.length && <p>当前没有可执行动作，可查看卡牌信息。</p>}
        </div>
      </dialog>
    </div>
  );
}
