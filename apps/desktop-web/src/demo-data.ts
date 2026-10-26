import type {
  CardIdentity,
  MatchSnapshot,
  ReplayPage,
  Seat,
} from "@ptcg/battle-protocol";

// Invented cards and public DTOs only. No catalogue, hidden zone contents or engine.
const unit = (id: string, name: string): CardIdentity => ({
  cardId: id,
  name,
  presentation: {
    id,
    name,
    hp: 90,
    maximumHp: 100,
    superType: "POKEMON",
    attacks: [
      {
        name: "演示招式",
        damage: 10,
        cost: [],
        text: "界面演示，不执行规则。",
      },
    ],
  },
});
export const scenarios = [
  "options",
  "selection",
  "board-selection",
  "public-history",
  "legacy-history",
  "stadium-history",
  "prize",
  "non-turn",
  "waiting",
  "finished",
] as const;
export type Scenario = (typeof scenarios)[number];
export function demoPublicHistory(seat: Seat, count = 3): MatchSnapshot[] {
  const first = demoSnapshot(seat);
  first.matchSeq = 0;
  first.events = [];
  first.observation.viewer = seat === "PLAYER1" ? "player1" : "player2";
  first.observation.turn = seat === "PLAYER1" ? "PLAYER2" : "PLAYER1";
  first.decision = null;
  const item = {
    cardId: "demo-item",
    name: "演示检索物品",
    presentation: { name: "演示检索物品" },
  };
  const frames = [first];
  for (let seq = 1; seq < count; seq++) {
    const frame = structuredClone(frames.at(-1)!);
    frame.matchSeq = seq;
    frame.stateVersion = seq;
    frame.events = [
      {
        seq,
        actor: first.observation.turn,
        kind: "action",
        text:
          seq === 1
            ? "使用物品 · 演示检索物品"
            : seq === 2
              ? "使用招式 · 演示宝可梦乙 · 演示招式"
              : "结束回合",
        cards:
          seq === 1
            ? [item]
            : seq === 2
              ? [frame.observation.opponent.active[0].card]
              : [],
      },
    ];
    if (seq === 1) {
      frame.publicReveals = [
        {
          kind: "search_reveal",
          actor: first.observation.turn!,
          cards: [{ id: "demo-hit", name: "演示公开宝可梦丙" }],
        },
      ];
      frame.observation.opponent.discard = [item];
      frame.observation.opponent.handCount = 2;
      frame.observation.opponent.deckCount = 39;
    } else if (seq === 2) {
      frame.events[0].coins = ["heads", "tails"];
      frame.observation.self.active[0].card.presentation!.hp = 30;
      frame.observation.self.active[0].card.presentation!.poisoned = true;
    } else {
      frame.observation.turn = seat;
      frame.observation.turnNumber = 3;
    }
    frames.push(frame);
  }
  return frames;
}
export function demoStadiumHistory(seat: Seat, count = 3): MatchSnapshot[] {
  const stadium: CardIdentity = { cardId: "demo-stadium", name: "演示竞技场" };
  return demoPublicHistory(seat, count).map((frame, seq) => {
    const first = demoSnapshot(seat);
    frame.observation = { ...structuredClone(first.observation),
      viewer: seat === "PLAYER1" ? "player1" : "player2",
      turn: seat === "PLAYER1" ? "PLAYER2" : "PLAYER1",
      stadium: [{ ref: "demo-stadium-ref", card: stadium }],
    };
    frame.publicReveals = [];
    frame.events = seq ? [{ seq, actor: frame.observation.turn, kind: "action",
      text: seq === 1 ? "使用竞技场效果 · 演示竞技场" : "竞技场效果 · 演示竞技场 · 完成选牌",
      cards: [stadium],
    }] : [];
    return frame;
  });
}

export function demoSnapshot(
  seat: Seat = "PLAYER1",
  scenario: Scenario = "options",
): MatchSnapshot {
  const opponent: Seat = seat === "PLAYER1" ? "PLAYER2" : "PLAYER1";
  const visible = unit("demo-visible", "演示宝可梦甲");
  const other = unit("demo-other", "演示宝可梦乙");
  const base = {
    active: [{ ref: "demo-active", card: visible }],
    bench: [],
    discard: [],
    lostZone: [],
    handCount: 1,
    deckCount: 40,
    prizeCount: 6,
    faceUpPrizes: [],
    benchCapacity: 5,
  };
  const value: MatchSnapshot = {
    protocolVersion: "2.0",
    matchId: "demo-match",
    ruleReleaseId: "demo-rules",
    viewerSeat: seat,
    matchSeq: 1,
    stateVersion: 1,
    controllerEpoch: 1,
    streamCursor: "demo-cursor-1",
    status: "playing",
    phase: "playing",
    done: false,
    winner: null,
    observation: {
      turn: scenario === "non-turn" ? opponent : seat,
      turnNumber: 2,
      self: { ...base, hand: [other] },
      opponent: {
        ...base,
        active: [{ ref: "demo-enemy", card: other }],
        hand: null,
      },
      stadium: [],
    },
    decision: {
      id: "demo-decision",
      actor: seat,
      kind: "options",
      options: [{ id: "demo-pass", actionType: "PassTurn" }],
    },
    events: [
      {
        seq: 1,
        actor: seat,
        kind: "action",
        text: "这是合成的裁定记录，仅验证界面展示。",
      },
    ],
  };
  if (scenario === "selection" || scenario === "board-selection")
    value.decision = {
      id: "demo-search",
      actor: seat,
      kind: "selection",
      min: 1,
      max: 2,
      hidden: false,
      indexed: true,
      instruction: "演示检索：选择卡牌后可以浏览完整牌库。",
      candidates: [
        { ref: "demo-choice-a", card: visible },
        { ref: "demo-choice-b", card: other },
      ],
      deckInspection: {
        owner: "self",
        ordered: false,
        total: 2,
        cards: [
          { card: visible, count: 1 },
          { card: other, count: 1 },
        ],
      },
    };
  if (scenario === "board-selection")
    value.decision = {
      id: "demo-board-choice",
      actor: seat,
      kind: "selection",
      min: 1,
      max: 1,
      hidden: false,
      indexed: false,
      instruction: "演示场上选牌：选择我方战斗宝可梦。",
      candidates: [
        { ref: "demo-choice-a", boardRef: "demo-active", card: visible },
      ],
    };
  if (scenario === "prize") {
    value.observation.self.faceUpPrizes = [{ index: 2, card: visible }];
    value.decision = {
      id: "demo-prize",
      actor: seat,
      kind: "selection",
      min: 1,
      max: 1,
      hidden: true,
      indexed: true,
      candidates: [
        { ref: "demo-hidden" },
        {
          ref: "demo-visible-prize",
          card: visible,
          revealedPrizeIndex: 2,
          revealedPrizeSide: "self",
        },
      ],
    };
  }
  if (scenario === "waiting") value.decision = null;
  if (scenario === "finished")
    Object.assign(value, {
      done: true,
      status: "finished",
      decision: null,
      winner: seat,
      result: { reason: "prizes_taken", conditions: ["prizes_taken"] },
    });
  return structuredClone(value);
}
export function demoReplay(seat: Seat): ReplayPage {
  return {
    protocolVersion: "2.0",
    replaySchemaVersion: "visible-replay-v2",
    matchId: "demo-match",
    ruleReleaseId: "demo-rules",
    viewerSeat: seat,
    nextCursor: null,
    frames: [0, 1, 2].map((seq) => {
      const {
        protocolVersion,
        matchId,
        ruleReleaseId,
        viewerSeat,
        controllerEpoch,
        streamCursor,
        decision,
        ...frame
      } = demoSnapshot(seat);
      return {
        ...frame,
        frameSeq: seq,
        matchSeq: seq,
        stateVersion: seq,
        events: seq ? frame.events : [],
      };
    }),
  };
}
