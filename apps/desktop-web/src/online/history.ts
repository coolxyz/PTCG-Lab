import type {
  CardIdentity,
  CardState,
  MatchSnapshot,
  ReplayFrame,
  ReplayPage,
} from "@ptcg/battle-protocol";
import type {
  BattleView,
  CardInfo,
  ReplayCheckpoint,
} from "@ptcg/battle-ui";
import { eventText } from "@ptcg/battle-ui/presentation";
import { card } from "../presentation";

type Frame = ReplayFrame | MatchSnapshot;
type Entry = NonNullable<BattleView["events"]>[number];
const zones: Record<string, string> = {
  hand: "手牌",
  deck: "牌库",
  prize: "奖赏卡",
  prizes: "奖赏卡",
  discard: "弃牌区",
  active: "战斗区",
  bench: "备战区",
  lost_zone: "放逐区",
  lostZone: "放逐区",
  stadium: "竞技场",
};
const conditions: Record<string, string> = {
  POISONED: "中毒",
  BURNED: "灼伤",
  ASLEEP: "睡眠",
  PARALYZED: "麻痹",
  CONFUSED: "混乱",
  NORMAL: "无特殊状态",
};
const identity = (c: CardIdentity) => c.cardId + ":" + c.name;
function addedCards(old: CardIdentity[], next: CardIdentity[]): CardIdentity[] {
  const counts = new Map<string, number>();
  for (const c of old)
    counts.set(identity(c), (counts.get(identity(c)) || 0) + 1);
  return next.filter((c) => {
    const n = counts.get(identity(c)) || 0;
    if (n) {
      counts.set(identity(c), n - 1);
      return false;
    }
    return true;
  });
}
function appended<T>(old: T[], next: T[]): T[] {
  // Reveal records are cumulative. A missing prefix must never be assigned to a
  // fabricated action; the caller labels the first available frame explicitly.
  return old.length <= next.length &&
    old.every((v, i) => JSON.stringify(v) === JSON.stringify(next[i]))
    ? next.slice(old.length)
    : [];
}
const stateConditions = (c: CardState) =>
  [
    ...(c.conditions || []),
    ...(c.card.presentation?.poisoned ? ["POISONED"] : []),
    ...(c.card.presentation?.burned ? ["BURNED"] : []),
    ...(c.card.presentation?.specialCondition &&
    c.card.presentation.specialCondition !== "NORMAL"
      ? [c.card.presentation.specialCondition]
      : []),
  ]
    .filter((v, i, all) => all.indexOf(v) === i)
    .sort();

const typeNames: Record<string, string> = {
  COLORLESS: "无色", FIRE: "火", WATER: "水", GRASS: "草", LIGHTNING: "雷",
  PSYCHIC: "超", FIGHTING: "斗", DARKNESS: "恶", DARK: "恶", METAL: "钢", DRAGON: "龙", FAIRY: "妖",
};

function publicCardChanges(before: CardState, after: CardState, label: string): string[] {
  const changes: string[] = [];
  const a = before.card.presentation, b = after.card.presentation;
  for (const [key, name, on, off] of [
    ["abilityUsed", "特性使用标记", "已使用", "已重置"],
    ["abilitiesSuppressed", "特性无效标记", "生效", "已清除"],
    ["retreatBlocked", "撤退限制", "生效", "已清除"],
    ["attackBlocked", "攻击限制", "生效", "已清除"],
  ] as const) {
    const old = before.flags ? before.flags.includes(key) : !!a?.[key];
    const next = after.flags ? after.flags.includes(key) : !!b?.[key];
    if (old !== next) changes.push(`${label} · ${name}：${next ? on : off}`);
  }
  for (const [key, name] of [
    ["poisonDamage", "中毒伤害"],
    ["attackDamageReduction", "受到招式伤害减少"],
    ["preventAttackDamageAtMost", "防止招式伤害阈值"],
    ["attackPowerReduction", "招式伤害降低"],
    ["attackPowerBonus", "招式伤害增加"],
  ] as const) {
    if (a?.[key] !== b?.[key])
      changes.push(`${label} · ${name}：${b?.[key] ?? "不再标注"}`);
  }
  if (a?.retaliationCounters !== b?.retaliationCounters)
    changes.push(`${label} · 受击反击：${b?.retaliationCounters !== undefined ? `${b.retaliationCounters / 10} 个伤害指示物` : "不再标注"}`);
  const maxBefore = before.maximumHp ?? a?.maximumHp,
    maxAfter = after.maximumHp ?? b?.maximumHp;
  if (maxBefore !== maxAfter)
    changes.push(`${label} · HP 上限：${maxBefore ?? "未标注"} → ${maxAfter ?? "未标注"}`);
  for (const [key, name] of [["limitedAttacks", "受限招式"], ["effectiveTypes", "当前属性"]] as const) {
    if (JSON.stringify([...(a?.[key] || [])].sort()) !== JSON.stringify([...(b?.[key] || [])].sort()))
      changes.push(`${label} · ${name}：${b?.[key]?.map((name) => key === "effectiveTypes" ? typeNames[name] || name : name).join("、") || "不再标注"}`);
  }
  for (const [key, name] of [["damage", "防止招式伤害"], ["effects", "防止招式效果"]] as const) {
    if (!!a?.attackProtection?.[key] !== !!b?.attackProtection?.[key])
      changes.push(`${label} · ${name}：${b?.attackProtection?.[key] ? "生效" : "已清除"}`);
  }
  if (a?.recovery?.amount !== b?.recovery?.amount || !!a?.recovery?.cure !== !!b?.recovery?.cure)
    changes.push(`${label} · 回复效果：${b?.recovery ? `回复 ${b.recovery.amount} HP${b.recovery.cure ? "，解除特殊状态" : ""}` : "不再标注"}`);
  return changes;
}

// A result headline describes an observed fact, never guesses which action caused it.
function resultHeadline(
  groups: NonNullable<Entry["cardGroups"]>,
  changes: string[],
  cards: Record<string, CardInfo>,
): string | undefined {
  const candidates = groups.filter((g) => g.cards.length).map((group) => {
    const names = group.cards.slice(0, 2).map((c) =>
      cards[c.id || ""]?.cnName || cards[c.name]?.cnName || c.name,
    );
    return {
      priority: /展示|公开的奖赏卡/.test(group.label) ? 100
        : /竞技场|战斗区|备战区/.test(group.label) ? 90
        : /弃牌区|放逐区/.test(group.label) ? 80 : 50,
      text: `${group.label}：${names.join("、")}${group.cards.length > 2 ? ` 等 ${group.cards.length} 张` : ""}`,
    };
  });
  for (const change of changes) {
    if (change.startsWith("前序记录")) continue;
    candidates.push({
      priority: /HP：|状态：/.test(change) ? 95
        : /特性|能量：|道具：|竞技场|防止|回复|招式伤害|受限招式/.test(change) ? 85
        : /限制|奖赏卡/.test(change) ? 75
        : /移出/.test(change) ? 70
        : /进入回合/.test(change) ? 60 : 40,
      text: eventText(change, cards),
    });
  }
  return candidates.sort((a, b) => b.priority - a.priority)[0]?.text;
}

const genericDeclarations = new Set([
  "可见局面更新", "完成一次效果选择", "完成开局准备步骤", "执行动作", "完成效果选项选择", "完成对局操作",
]);

/** Uses authorized frames only. No inference from hidden choices or opponent hand identities. */
export function visibleHistory(
  frames: Frame[],
  cards: Record<string, CardInfo>,
): Entry[] {
  const ordered = [
    ...new Map(frames.map((f) => [f.matchSeq, f])).values(),
  ].sort((a, b) => a.matchSeq - b.matchSeq);
  const cn = (name?: string | null) =>
    name ? cards[name]?.cnName || name : "未公开卡牌";
  return ordered.slice(-200).map((frame) => {
    const index = ordered.indexOf(frame),
      previous = ordered[index - 1];
    const adjacent = previous?.matchSeq + 1 === frame.matchSeq;
    const authoritative = frame.events.filter((e) => e.seq === frame.matchSeq);
    const viewer = "viewerSeat" in frame
      ? frame.viewerSeat
      : frame.observation.viewer?.toUpperCase();
    const actorLabel = (seat: string) => viewer
      ? (seat === viewer ? "你" : "对手")
      : (seat === "PLAYER1" ? "玩家 1" : "玩家 2");
    const recovered = !adjacent && frame.matchSeq > 0;
    const groups: NonNullable<Entry["cardGroups"]> = [];
    const changes: string[] = [];
    for (const e of authoritative)
      if (e.cards?.length)
        groups.push({
          label: e.kind === "reveal" ? "公开展示" : "使用 / 涉及的卡牌",
          cards: e.cards.map(card),
        });
    // Persisted rule-authorized reveals are the only source of revealed hand/search identities.
    const reveals = previous
      ? appended(previous.publicReveals || [], frame.publicReveals || [])
      : frame.publicReveals || [];
    for (const reveal of reveals) {
      const actor = actorLabel(reveal.actor);
      const reason =
        reveal.kind === "hand_reveal"
          ? "展示手牌"
          : reveal.kind === "search_reveal"
            ? "按效果展示卡牌"
            : "公开展示";
      groups.push({
        label: `${actor} · ${reason}${recovered ? "（此前已公开，具体步骤待补齐）" : ""}`,
        cards: reveal.cards.map((c) => ({
          ...c,
          name: c.name,
          id: c.id,
        })) as NonNullable<Entry["cardGroups"]>[number]["cards"],
      });
      if (!recovered) changes.push(`${actor}${reason}：${reveal.cards.length} 张`);
    }
    const setups = previous
      ? appended(previous.setupEvents || [], frame.setupEvents || [])
      : frame.setupEvents || [];
    for (const setup of setups)
      if (setup.kind === "mulligan")
        for (const [seat, hand] of Object.entries(setup.hands || {}))
          if (hand?.length)
            groups.push({
              label: `${actorLabel(seat)} · 重抽前展示的手牌${recovered ? "（此前已公开，具体步骤待补齐）" : ""}`,
              cards: hand as NonNullable<Entry["cardGroups"]>[number]["cards"],
            });
    for (const effect of frame.visualEffects || []) {
      // Counts are compared below even if some older frames have partial effects.
      if (effect.kind !== "count" && effect.kind !== "turn") continue;
      if (effect.kind === "turn")
        changes.push(`进入回合 ${frame.observation.turnNumber}`);
    }
    if (adjacent) {
      if (previous.observation.turn !== frame.observation.turn || previous.observation.turnNumber !== frame.observation.turnNumber)
        changes.push(`进入回合 ${frame.observation.turnNumber}`);
      for (const side of ["self", "opponent"] as const) {
        const owner = side === "self" ? "你" : "对手",
          old = previous.observation[side],
          next = frame.observation[side];
        if (old.benchCapacity !== next.benchCapacity)
          changes.push(`${owner}备战区容量：${old.benchCapacity} → ${next.benchCapacity}`);
        const prizes = next.faceUpPrizes.filter((p) => !old.faceUpPrizes.some(
          (q) => p.index === q.index && identity(p.card) === identity(q.card),
        ));
        if (prizes.length) groups.push({ label: `${owner}公开的奖赏卡`, cards: prizes.map((p) => card(p.card)) });
        for (const p of old.faceUpPrizes)
          if (!next.faceUpPrizes.some((q) => q.index === p.index && identity(q.card) === identity(p.card)))
            changes.push(`${owner}奖赏卡位置 ${p.index + 1} 的原展示记录已移除`);
        for (const [key, label] of [
          ["handCount", "手牌"],
          ["deckCount", "牌库"],
          ["prizeCount", "奖赏卡"],
        ] as const)
          if (old[key] !== next[key])
            changes.push(`${owner}的${label}数量：${old[key]} → ${next[key]}`);
        for (const zone of ["discard", "lostZone"] as const) {
          const added = addedCards(old[zone], next[zone]);
          const removed = addedCards(next[zone], old[zone]);
          if (added.length)
            groups.push({
              label: `${owner}${zones[zone]}新增`,
              cards: added.map(card),
            });
          if (removed.length)
            changes.push(
              `${owner}${zones[zone]}移出：${removed.map((c) => cn(c.name)).join("、")}`,
            );
        }
        for (const [key, title] of [
          ["itemsBlocked", "物品使用限制"],
          ["attacksBlocked", "攻击限制"],
          ["energyPlayed", "本回合通常附能已使用"],
          ["supporterPlayed", "本回合支援者已使用"],
          ["retreated", "本回合通常撤退已使用"],
        ] as const) {
          if (!!old.turnFlags?.[key] !== !!next.turnFlags?.[key])
            changes.push(
              `${owner}：${title}${next.turnFlags?.[key] ? "（生效）" : "（解除 / 重置）"}`,
            );
        }
        for (const zone of ["active", "bench"] as const) {
          const added = addedCards(
            old[zone].map((c) => c.card),
            next[zone].map((c) => c.card),
          );
          if (added.length)
            groups.push({
              label: `${owner}${zones[zone]}出现`,
              cards: added.map(card),
            });
          if (old[zone].length !== next[zone].length)
            changes.push(
              `${owner}${zones[zone]}数量：${old[zone].length} → ${next[zone].length}`,
            );
          next[zone].forEach((state, i) => {
            const prior = old[zone][i];
            // Slots/refs aren't permanent instance IDs. Avoid attributing damage
            // to a replacement or a moved duplicate when either lineup changed.
            if (
              !prior ||
              old[zone].length !== next[zone].length ||
              !old[zone].every(
                (c, j) => identity(c.card) === identity(next[zone][j].card),
              )
            )
              return;
            const label = `${owner}${zones[zone]}${zone === "bench" ? ` ${i + 1}` : ""} · ${cn(state.card.name)}`;
            changes.push(...publicCardChanges(prior, state, label).map((text) => eventText(text, cards, [card(state.card)])));
            const beforeHP = prior.hp ?? prior.card.presentation?.hp,
              afterHP = state.hp ?? state.card.presentation?.hp;
            if (
              typeof beforeHP === "number" &&
              typeof afterHP === "number" &&
              beforeHP !== afterHP
            )
              changes.push(
                `${label} HP：${Math.max(0, beforeHP)} → ${Math.max(0, afterHP)}`,
              );
            const a = stateConditions(prior),
              b = stateConditions(state);
            if (a.join() !== b.join())
              changes.push(
                `${label}状态：${b.length ? b.map((s) => conditions[s] || s).join("、") : "无特殊状态"}`,
              );
            for (const field of ["energy", "tool"] as const) {
              const before =
                field === "energy"
                  ? (prior.energy ?? prior.card.presentation?.energy ?? [])
                  : (prior.tools?.map((c) => c.name) ??
                    prior.card.presentation?.tool ??
                    []);
              const after =
                field === "energy"
                  ? (state.energy ?? state.card.presentation?.energy ?? [])
                  : (state.tools?.map((c) => c.name) ??
                    state.card.presentation?.tool ??
                    []);
              if (
                JSON.stringify([...before].sort()) !==
                JSON.stringify([...after].sort())
              )
                changes.push(
                  `${label}${field === "energy" ? "能量" : "道具"}：${after.length ? after.map(cn).join("、") : "无"}`,
                );
            }
          });
        }
      }
      const stadium = addedCards(
        previous.observation.stadium.map((c) => c.card),
        frame.observation.stadium.map((c) => c.card),
      );
      if (stadium.length)
        groups.push({ label: "竞技场变更", cards: stadium.map(card) });
      else if (
        previous.observation.stadium.length &&
        !frame.observation.stadium.length
      )
        changes.push("竞技场已移除");
    }
    if (!adjacent && previous)
      changes.push("前序记录尚未补齐，暂不推断跨步变化。");
    const sourceCards = groups.flatMap((g) => g.cards);
    const event = authoritative[0];
    const declarations = authoritative
      .filter((e) => e.text.trim() && !genericDeclarations.has(e.text.trim()))
      .map((e) => eventText(e.text, cards, sourceCards));
    const coins = authoritative.flatMap((e) => e.coins || []);
    const coinTitle = coins.length
      ? `投币结果：${coins.slice(0, 5).map((c) => c === "heads" ? "正面" : "反面").join("、")}${coins.length > 5 ? ` 等 ${coins.length} 次` : ""}`
      : undefined;
    const result = (adjacent ? resultHeadline(groups, changes, cards) : undefined) || coinTitle;
    const title = declarations.length ? declarations.join("；")
      : frame.done ? "对局结束"
      : frame.matchSeq === 0 ? "对局开始 · 开局准备"
      : result || (recovered ? `恢复对局记录 · 第 ${frame.matchSeq} 步`
        : frame.phase !== "playing" ? "完成开局准备步骤"
        : event ? (event.text === "完成效果选项选择" ? "已完成效果选项选择" : event.text === "完成对局操作" ? "已完成对局操作" : "已完成选牌")
        : `对局步骤 ${frame.matchSeq} · 动作详情未记录`);
    return {
      seq: frame.matchSeq,
      actor: event?.actor || "system",
      turn: event && !adjacent && frame.matchSeq > 0
        ? undefined
        : event && previous
          ? previous.observation.turnNumber
          : frame.observation.turnNumber,
      kind: event?.kind || (groups.length ? "reveal" : "changes"),
      text: title,
      coins,
      cardGroups: groups,
      changes: [...new Set(changes)],
      explanation: !event
        ? "此步未保存动作声明，仅展示已确认的可见信息。"
        : !declarations.length && !result && adjacent && !frame.done
          ? "本步骤未记录更多公开信息；不能据此判断效果未发生或未找到目标。"
          : undefined,
    };
  });
}

export function replayTimeline(page: ReplayPage): ReplayCheckpoint[] {
  return page.frames.flatMap((frame, index, all) =>
    !index ||
    frame.phase !== all[index - 1].phase ||
    frame.observation.turnNumber !== all[index - 1].observation.turnNumber ||
    frame.observation.turn !== all[index - 1].observation.turn
      ? [
          {
            seq: index,
            turn: frame.observation.turnNumber,
            player: frame.observation.turn,
            phase: frame.phase === "playing" ? "playing" : "setup",
          },
        ]
      : [],
  );
}
