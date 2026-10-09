import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Swords,
  RefreshCw,
  History,
  ChevronLeft,
  ChevronRight,
  Flag,
} from "lucide-react";
import "./battle.css";
import { Tabletop } from "./Tabletop";
import type { ChineseDetails } from "./CardArtwork";

export type CardInfo = {
  cnName: string;
  englishName: string;
  engineId: string;
  effectStatus: string;
  reviewNote: string;
  pendingSourceException?: boolean;
  chineseDetails?: ChineseDetails;
  image: { url: string | null; label: string; userUploaded?: boolean };
};
export type EngineCard = {
  name: string;
  id?: string;
  ref?: string;
  evolved?: string[];
  abilityUsed?: boolean;
  hp?: number;
  maximumHp?: number;
  abilitiesSuppressed?: boolean;
  attackPowerReduction?: number;
  attackPowerBonus?: number;
  retaliationCounters?: number;
  energy?: string[];
  tool?: string[];
  specialCondition?: string;
  retreatBlocked?: boolean;
  attackBlocked?: boolean;
  poisoned?: boolean;
  poisonDamage?: number;
  burned?: boolean;
  attackDamageReduction?: number;
  preventAttackDamageAtMost?: number;
  limitedAttacks?: string[];
  attackProtection?: {damage?: boolean; effects?: boolean; source?: string};
  attacks?: { name: string; damage: number; text: string }[];
  abilities?: { name: string; text: string }[];
  kind?: string;
  trainerType?: string;
  superType?: string;
  damage?: number;
};
export type Side = {
  turnFlags?: {itemsBlocked?: boolean; attacksBlocked?: boolean};
  benchCapacity?: number;
  active: EngineCard[];
  bench: EngineCard[];
  hand: EngineCard[] | null;
  hand_count: number;
  deck_count: number;
  prize_count: number;
  faceUpPrizes?: (EngineCard & { prizeIndex: number })[];
  discard: EngineCard[];
  lost_zone: EngineCard[];
};
export type Option = {
  id: string;
  actionType: string;
  label?: string;
  kind?: string;
  value?: string | number | boolean;
  source?: string;
  sourceRef?: string;
  targetRef?: string;
  active_pokemonRef?: string;
  target?: string;
  sourcePosition?: string;
  sourceIndex?: number;
  targetPosition?: string;
  targetIndex?: number;
  active_pokemon?: string;
  ability?: string;
  chosen?: string[];
  attack?: { name: string; damage: number };
};
export type Decision = {
  id: string;
  kind: string;
  min: number;
  max: number;
  hidden: boolean;
  indexed?: boolean;
  source?: string;
  instruction?: string;
  candidates: {
    ref: string;
    boardRef?: string;
    card?: EngineCard;
    side?: string;
    position?: string;
    index?: number;
  }[];
  options: Option[];
};
export type BattleView = {
  aiVersion?: string;
  result?: { reason: string; conditions: string[] } | null;
  matchId: string;
  stateVersion: number;
  status: string;
  done: boolean;
  winner: string | null;
  phase: string;
  decision: Decision | null;
  observation: {
    turn: string;
    turn_number: number;
    self: Side;
    opponent: Side;
    stadium: EngineCard[];
    termination_reason: string | null;
  };
  events?: {
    seq: number;
    actor: string;
    text: string;
    coins?: ("heads" | "tails")[];
    actionType?: string;
    sourceLocation?: string;
    targetLocation?: string;
    active_pokemonLocation?: string;
    explanation?: string;
    changes?: string[];
    effects?: {
      kind: "count" | "hp" | "move" | "turn";
      side: string;
      zone: string;
      index?: number;
      before: number;
      after: number;
      fromZone?: string;
      cardName?: string | null;
      label?: string;
    }[];
  }[];
  setupEvents?: any[];
  publicReveals?: any[];
};
type Command = {
  commandId: string;
  expectedStateVersion: number;
  decisionId: string;
  choice: { optionId?: string; selectedRefs?: string[] };
};
type ReplayCheckpoint = {
  seq: number;
  turn: number;
  player: string | null;
  phase: string;
};
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
async function request(path: string, body?: unknown) {
  const response = await fetch("/api/battle" + path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.message || data.detail?.[0]?.msg || "请求失败，请刷新重试",
    );
  return data;
}

export function Battle({
  cards,
  onBuild,
}: {
  cards: CardInfo[];
  onBuild: () => void;
}) {
  const [session, setSession] = useState<any>(null),
    [revisions, setRevisions] = useState<any[]>([]),
    [matches, setMatches] = useState<any[]>([]);
  const [revision, setRevision] = useState(""),
    [opponent, setOpponent] = useState("");
  const [aiLevel, setAiLevel] = useState("A1");
  const [view, setView] = useState<BattleView | null>(null),
    [frame, setFrame] = useState<BattleView | null>(null);
  const [replaySeq, setReplaySeq] = useState(0),
    [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [resignConfirm, setResignConfirm] = useState(false);
  const [autoplay, setAutoplay] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [aiDelay, setAiDelay] = useState(1800);
  const [aiPaused, setAiPaused] = useState(false);
  const [coinPlaying, setCoinPlaying] = useState(false);
  const [timeline, setTimeline] = useState<ReplayCheckpoint[]>([]);
  const [replayEnd, setReplayEnd] = useState(0);
  const [animateReplay, setAnimateReplay] = useState(false);
  const [filter, setFilter] = useState(""),
    [retry, setRetry] = useState<Command | null>(null);
  const locked = useRef(false);
  const createId = useRef(crypto.randomUUID());
  const names = useMemo(
    () =>
      Object.fromEntries(
        (session?.cards || cards)
          .filter((c: CardInfo) => c.effectStatus === "verified")
          .sort((a: CardInfo, b: CardInfo) => {
            const score = (c: CardInfo) => (c.image.userUploaded ? 100 : c.image.url ? 10 : 0) + (c.chineseDetails?.sections?.length ? 1 : 0);
            return score(a) - score(b);
          })
          .flatMap((c: CardInfo) => [[c.englishName, c], [c.engineId, c]]),
      ),
    [cards, session],
  );
  const cn = (name: string | undefined) =>
    name ? names[name]?.cnName || name : "";
  async function run(work: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setAutoplay(false);
      setError(e instanceof Error ? e.message : "网络中断，请重试");
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function loadLobby() {
    const [rs, ms] = await Promise.all([
      request("/revisions"),
      request("/matches"),
    ]);
    setRevisions(rs);
    setMatches(ms);
    setRevision(
      (old) =>
        old ||
        rs.find(
          (r: any) =>
            r.playable &&
            r.id === sessionStorage.getItem("ptcg-preferred-revision"),
        )?.id ||
        rs.find((r: any) => r.playable)?.id ||
        "",
    );
  }
  useEffect(() => {
    void run(async () => {
      setSession(await request("/session", {}));
      await loadLobby();
      const saved = localStorage.getItem("ptcg-match");
      if (saved) {
        try {
          setView(await request("/matches/" + saved));
        } catch {
          localStorage.removeItem("ptcg-match");
        }
      }
    });
  }, []);
  useEffect(() => {
    const choices = [
      ...(session?.opponents || []).map((t: any) => t.id),
      ...revisions.filter((r) => r.playable).map((r) => `revision:${r.id}`),
    ];
    setOpponent((old) => choices.includes(old) ? old : choices.includes("dragapult") ? "dragapult" : choices[0] || "");
  }, [session, revisions]);
  useEffect(() => {
    setSelected([]);
    setFilter("");
    setResignConfirm(false);
  }, [view?.matchId, view?.stateVersion]);
  useEffect(() => {
    if (!view || view.done || view.decision || busy || error || frame || retry || aiPaused || coinPlaying)
      return;
    const timer = window.setTimeout(
      () =>
        void run(async () =>
          setView(await request(`/matches/${view.matchId}/advance?steps=1`, {})),
        ),
      aiDelay,
    );
    return () => window.clearTimeout(timer);
  }, [view, busy, error, frame, retry, aiDelay, aiPaused, coinPlaying]);
  async function open(id: string) {
    setAutoplay(false);
    await run(async () => {
      setView(await request("/matches/" + id));
      setFrame(null);
      setRetry(null);
      localStorage.setItem("ptcg-match", id);
    });
  }
  async function submit(choice: Command["choice"], existing?: Command) {
    if (!view?.decision && !existing) return;
    const command = existing || {
      commandId: "client-" + crypto.randomUUID(),
      expectedStateVersion: view!.stateVersion,
      decisionId: view!.decision!.id,
      choice,
    };
    setRetry(command);
    await run(async () => {
      const result = await request(
        `/matches/${view!.matchId}/commands`,
        command,
      );
      setView(result.view);
      setRetry(null);
    });
  }
  async function refresh() {
    await run(async () => {
      if (view) {
        setView(await request("/matches/" + view.matchId));
        setRetry(null);
      } else {
        setSession(await request("/session", {}));
        await loadLobby();
      }
    });
  }
  async function showReplay(seq: number, automatic = false) {
    if (!view) return;
    if (!automatic) setAutoplay(false);
    await run(async () => {
      if (!frame) {
        const index = await request(`/matches/${view.matchId}/replay/timeline`);
        setTimeline(index.checkpoints);
        setReplayEnd(index.lastSeq);
        setSelected([]);
        setResignConfirm(false);
      }
      const data = await request(
        `/matches/${view.matchId}/replay?after=${seq - 1}&limit=1`,
      );
      const entry = data.frames[0];
      if (entry) {
        setFrame({ ...entry.view, events: [entry.event] });
        setReplaySeq(entry.seq);
        setAnimateReplay(automatic);
      } else throw new Error("未找到该回放步骤，请刷新后重试");
    });
  }
  useEffect(() => {
    if (!autoplay || !frame || busy || error) return;
    if (replaySeq >= replayEnd) {
      setAutoplay(false);
      return;
    }
    const timer = setTimeout(
      () => void showReplay(replaySeq + 1, true),
      1000 / speed,
    );
    return () => clearTimeout(timer);
  }, [autoplay, frame, busy, replaySeq, speed, error, replayEnd]);
  function optionText(o: Option) {
    if (o.actionType === "ChooseCardAction") {
      const counts = new Map<string, number>();
      for (const name of o.chosen || []) counts.set(name, (counts.get(name) || 0) + 1);
      return counts.size
        ? "弃置能量：" + [...counts].map(([name, quantity]) => `${cn(name)} ×${quantity}`).join("、")
        : "无需弃置能量，继续撤退";
    }
    if (o.actionType === "NumberOption")
      return o.label ?? `选择 ${o.value}`;
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
    return `${actionNames[o.actionType] || o.actionType}${o.source ? " · " + cn(o.source) + position(o.sourcePosition, o.sourceIndex) : ""}${o.target ? " → " + cn(o.target) + position(o.targetPosition, o.targetIndex) : ""}${o.attack ? " · " + o.attack.name + (o.attack.damage ? " / " + o.attack.damage : "") : ""}${o.ability ? " · " + o.ability : ""}${o.active_pokemon ? " · " + cn(o.active_pokemon) : ""}`;
  }
  const shown = frame || view;
  const d = shown?.decision;
  const previousTurn = timeline.filter((point) => point.seq < replaySeq).at(-1);
  const nextTurn = timeline.find((point) => point.seq > replaySeq);
  const currentTurn = timeline.filter((point) => point.seq <= replaySeq).at(-1);
  return (
    <section className="battle-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">PRACTICE ROOM</span>
          <h1>人机练习</h1>
          <p>支持卡池自由组牌 · A1 启发式 / A2 实验搜索 · 简中规则快照</p>
        </div>
        <button className="secondary" disabled={busy} onClick={refresh}>
          <RefreshCw size={15} />
          刷新局面
        </button>
      </div>
      {error && (
        <div role="alert" className="error-banner">
          {error}
          {retry && (
            <button disabled={busy} onClick={() => submit(retry.choice, retry)}>
              重试同一命令
            </button>
          )}
        </div>
      )}
      {!shown ? (
        <>
          <div className="battle-lobby">
            <div>
              <h2>开始一局</h2>
              <p>
                选择已保存的卡组版本。相同卡表复用原版本，按当前规则自动校验；修改卡表才生成新版本。
              </p>
              <p data-testid="battle-pool-status">
                对战范围：G/H/I/J 标记及八种基本能量。当前支持 {cards.filter((card) => card.effectStatus === "verified").length} 个卡牌版本自由组合。
                待核实例外 {cards.filter(card => card.pendingSourceException).length} 个，不计入已支持数量；未确认资料及范围外卡牌暂不可对战。
              </p>
              <label>
                我的卡组版本
                <select
                  aria-label="对战卡组版本"
                  value={revision}
                  onChange={(e) => {
                    setRevision(e.target.value);
                    createId.current = crypto.randomUUID();
                  }}
                >
                  <option value="">请选择版本</option>
                  {revisions.map((r) => (
                    <option key={r.id} value={r.id} disabled={!r.playable}>
                      {r.name} · 版本 {r.number}
                      {r.playable ? "" : `（${r.reason || "当前规则下不可对战"}）`}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                AI 卡组
                <select
                  aria-label="AI 卡组"
                  value={opponent}
                  onChange={(e) => {
                    setOpponent(e.target.value);
                    createId.current = crypto.randomUUID();
                  }}
                >
                  <optgroup label="我的卡组版本">
                    {revisions
                      .filter((r) => r.playable)
                      .map((r) => (
                        <option key={r.id} value={`revision:${r.id}`}>
                          {r.name} · 版本 {r.number}
                        </option>
                      ))}
                  </optgroup>
                  {session?.opponents.map((t: any) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                AI 策略
                <select
                  aria-label="AI 策略"
                  value={aiLevel}
                  onChange={(e) => {
                    setAiLevel(e.target.value);
                    createId.current = crypto.randomUUID();
                  }}
                >
                  <option value="A1">A1 · 启发式</option>
                  <option value="A2">A2 · 实验搜索（强度未评级）</option>
                </select>
              </label>
              <button
                className="primary"
                disabled={busy || !revision || !session || !opponent}
                onClick={() =>
                  run(async () => {
                    const v = await request("/matches", {
                      requestId: createId.current,
                      revisionId: revision,
                      aiLevel,
                      ...(opponent.startsWith("revision:")
                        ? { opponentRevisionId: opponent.slice(9) }
                        : { opponent }),
                    });
                    setView(v);
                    localStorage.setItem("ptcg-match", v.matchId);
                    createId.current = crypto.randomUUID();
                  })
                }
              >
                <Swords size={16} />
                开始人机对战
              </button>
              <button className="secondary" onClick={onBuild}>
                前往构筑并保存版本
              </button>
            </div>
            <div>
              <h2>练习说明</h2>
              <p>双方通过相同规则接口操作；AI 只能看到自己的手牌和公开场面。</p>
              <p>
                每一步裁定均保存。刷新页面、重启服务后可继续；离开练习页时暂停
                AI 推进。
              </p>
              <p>
                这是受控卡池的普通档练习，尚未进行竞技强度认证。数据及会话保存在本机浏览器；清除
                Cookie 后无法通过新会话访问原对局。
              </p>
            </div>
          </div>
          <section className="battle-history">
            <h2>我的对局</h2>
            {matches.length === 0 && <p>还没有对局</p>}
            {matches.map((m) => (
              <button
                className="secondary"
                key={m.id}
                disabled={busy}
                onClick={() => open(m.id)}
              >
                {m.opponent.startsWith("revision:")
                  ? "自定义 AI 卡组"
                  : m.opponent === "dragapult"
                    ? "多龙巴鲁托ex"
                    : "赛富豪ex"}{" "}
                · {m.status === "playing" ? "继续对局" : "查看结果 / 回放"} ·{" "}
                {m.seq} 步 <ChevronRight size={14} />
              </button>
            ))}
          </section>
        </>
      ) : (
        <>
          <div className="battle-toolbar">
            <strong>
              {frame
                ? `回放 · 第 ${replaySeq} 步`
                : `${phases[shown.phase] || shown.phase} · 回合 ${shown.observation.turn_number} · 第 ${shown.stateVersion} 步`}
            </strong>
            <div>
              {!frame && <div className="ai-playback-controls">
                <label>AI 行动速度 <select aria-label="AI 行动速度" value={aiDelay} onChange={e => setAiDelay(Number(e.target.value))}>
                  <option value={3000}>慢速 · 3 秒</option>
                  <option value={1800}>标准 · 1.8 秒</option>
                  <option value={1200}>快速 · 1.2 秒</option>
                </select></label>
                <button className="secondary" disabled={shown.done} aria-pressed={aiPaused} onClick={() => setAiPaused(p => !p)}>{aiPaused ? "继续 AI" : "暂停 AI"}</button>
                {aiPaused && <button className="secondary" disabled={busy || !!view?.decision || !!retry || shown.done} onClick={() => run(async () => { setView(await request(`/matches/${view!.matchId}/advance?steps=1`, {})); })}>AI 下一步</button>}
              </div>}
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    setView(null);
                    setFrame(null);
                    setAutoplay(false);
                    setRetry(null);
                    localStorage.removeItem("ptcg-match");
                    await loadLobby();
                  })
                }
              >
                返回大厅
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => showReplay(0)}
              >
                <History size={14} />
                逐步回放
              </button>
              {!view?.done && !frame && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => setResignConfirm(true)}
                >
                  <Flag size={14} />
                  认输
                </button>
              )}
            </div>
          </div>
          {resignConfirm && (
            <div className="battle-confirm" role="alert">
              确认认输并结束本局？
              <button
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    setView(
                      await request(`/matches/${view!.matchId}/resign`, {
                        expectedStateVersion: view!.stateVersion,
                      }),
                    );
                    setResignConfirm(false);
                  })
                }
              >
                确认认输
              </button>
              <button onClick={() => setResignConfirm(false)}>继续对战</button>
            </div>
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
                onClick={() => setAutoplay(!autoplay)}
              >
                {autoplay ? "暂停回放" : "播放回放"}
              </button>
              <select
                aria-label="回放速度"
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
              >
                <option value={1}>1×</option>
                <option value={2}>2×</option>
                <option value={4}>4×</option>
              </select>
              <button
                disabled={busy}
                onClick={() => {
                  setFrame(null);
                  setAutoplay(false);
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
                      : `回合 ${point.turn} · ${point.player?.toUpperCase() === "PLAYER1" ? "你" : "AI"}`}{" "}
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
                : shown.winner === "PLAYER1"
                  ? "你赢了！"
                  : shown.winner === "PLAYER2"
                    ? shown.status === "resigned"
                      ? "你已认输"
                      : "AI 获胜"
                    : "对局结束"}
              <small>
                {(
                  {
                    prizes_taken: "已拿完全部奖赏卡",
                    no_pokemon: "落败方场上已无宝可梦",
                    deck_out: "回合开始时无法抽牌",
                    resigned: "认输结束",
                    truncated: "达到步数保护上限，不计作正常胜负",
                    archived: "旧引擎版本已停用，仅供回放；请选择符合当前规则的卡组版本开始新对局",
                  } as Record<string, string>
                )[
                  shown.result?.reason ||
                    shown.observation.termination_reason ||
                    ""
                ] || "可查看逐步回放"}
                {` · 剩余奖赏：你 ${shown.observation.self.prize_count} / AI ${shown.observation.opponent.prize_count}`}
              </small>
            </div>
          )}
          <div className="battle-layout">
            <div className="battle-table">
              <Tabletop
                key={`${shown.matchId}:${frame ? "replay" : "live"}`}
                view={shown}
                animateEvents={!frame || animateReplay}
                cards={names}
                onCoinPlaying={setCoinPlaying}
                locked={busy || !!frame || !!retry || shown.done}
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
              {!!shown.setupEvents?.filter((e) => e.kind === "mulligan")
                .length && (
                <details className="battle-public">
                  <summary>开局重抽公开记录</summary>
                  {shown.setupEvents
                    .filter((e) => e.kind === "mulligan")
                    .map((e, i) => (
                      <p key={i}>
                        {Object.entries(e.hands)
                          .map(
                            ([p, c]: any) =>
                              `${p === "PLAYER1" ? "你" : "AI"}：${c.map((x: any) => cn(x.name)).join("、")}`,
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
                      {e.actor === "PLAYER1" ? "你" : "AI"}：
                      {e.cards.map((c: any) => cn(c.name)).join("、")}
                    </p>
                  ))}
                </details>
              )}
            </div>
            <aside className="battle-controls">
              <section className="decision-panel" aria-label="当前决策">
                <h2>
                  {frame
                    ? "历史决策（只读）"
                    : shown.done
                      ? "本局已结束"
                      : d?.kind === "selection" && d.max === 0
                        ? "查看卡牌"
                      : d
                        ? "轮到你选择"
                        : "AI 正在操作…"}
                </h2>
                {d?.kind === "selection" && (
                  <>
                    <p>
                      {d.instruction || phases[shown.phase]}
                      {d.source ? " · " + cn(d.source) : ""}
                    </p>
                    {d.max === 0 ? <p>以下是本次效果查看到的卡牌，无需选牌。查看完毕后继续。</p> : <p>
                      请选择 {d.min === d.max ? d.min : `${d.min}–${d.max}`}{" "}
                      张。
                      {d.hidden
                        ? "卡牌身份隐藏。"
                        : "选择后，后续效果会继续提示。"}
                    </p>}
                    {d.source === "Tatsugiri" && !d.hidden && (
                      <p className="inspection-summary" role="status">
                        {d.max === 0
                          ? `这 ${d.candidates.length} 张中有 ${d.candidates.filter(c => c.card?.trainerType === "SUPPORTER").length} 张支援者${d.candidates.some(c => c.card?.trainerType === "SUPPORTER") ? `：${d.candidates.filter(c => c.card?.trainerType === "SUPPORTER").map(c => cn(c.card?.name)).join("、")}。继续后可从中选择一张。` : "。继续后洗牌，不加入手牌。"}`
                          : "仅从刚才查看的卡牌中选择支援者，不会另外检索牌库。"}
                      </p>
                    )}
                    {d.max === 0 ? (
                      <ul className="inspection-cards" aria-label="本次查看的卡牌">
                        {d.candidates.map((c, i) => (
                          <li key={c.ref} className={!d.hidden && c.card?.trainerType === "SUPPORTER" ? "inspection-supporter" : ""}>
                            <strong>{d.hidden ? `背面卡牌 ${i + 1}` : cn(c.card?.name)}</strong>
                            {!d.hidden && <span>{c.card?.trainerType === "SUPPORTER" ? "支援者" : c.card?.trainerType === "ITEM" ? "物品" : c.card?.trainerType === "TOOL" ? "宝可梦道具" : c.card?.superType === "ENERGY" ? "能量" : c.card?.superType === "POKEMON" ? "宝可梦" : ""}</span>}
                          </li>
                        ))}
                      </ul>
                    ) : <div className="decision-candidates">
                      {d.candidates.map((c, i) => (
                        <button
                          key={c.ref}
                          className={selected.includes(c.ref) ? "selected" : ""}
                          disabled={
                            busy ||
                            !!frame ||
                            !!retry ||
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
                            `背面卡牌 ${i + 1}`
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
                    </div>}
                    <button
                      className="primary"
                      disabled={
                        busy ||
                        !!frame ||
                        !!retry ||
                        selected.length < d.min ||
                        selected.length > d.max
                      }
                      onClick={() => submit({ selectedRefs: selected })}
                    >
                      {d.max === 0 ? "已查看，继续结算" : `确认选择 ${selected.length} 张`}
                    </button>
                    {d.max === 0 && <small>这些卡牌仅供查看，无需选中。确认后继续处理效果。</small>}
                    {d.min === 0 && d.max > 0 && (
                      <small>允许不选，点击“确认选择 0 张”继续。</small>
                    )}
                  </>
                )}
                {d?.kind === "options" &&
                  d.options.length > 0 &&
                  d.options.every((o) => o.actionType === "NumberOption") && (
                    <p>请选择要执行的效果。</p>
                  )}
                {d?.kind === "options" && (
                  <>
                    <input
                      aria-label="筛选合法动作"
                      placeholder="筛选卡名或动作"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    />
                    <div className="decision-options">
                      {d.options
                        .filter((o) =>
                          optionText(o)
                            .toLowerCase()
                            .includes(filter.toLowerCase()),
                        )
                        .map((o) => (
                          <button
                            key={o.id}
                            disabled={busy || !!frame || !!retry}
                            onClick={() => submit({ optionId: o.id })}
                          >
                            {optionText(o)}
                          </button>
                        ))}
                    </div>
                  </>
                )}
                {!d && !shown.done && (
                  <p>每次 AI 决策均经过服务端校验，轮到你时会显示可选动作。</p>
                )}
              </section>
              <section className="battle-log">
                <h3>裁定记录</h3>
                <ol>
                  {[...(shown.events || [])].reverse().map((e) => (
                    <li key={e.seq}>
                      <small>
                        #{e.seq} ·{" "}
                        {e.actor === "ai"
                          ? "AI"
                          : e.actor === "you"
                            ? "你"
                            : "系统"}
                      </small>
                      <span>{e.text}</span>
                      {e.changes?.map((change, i) => (
                        <small key={i}>{change}</small>
                      ))}
                      {e.explanation && <small>{e.explanation}</small>}
                    </li>
                  ))}
                </ol>
              </section>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
