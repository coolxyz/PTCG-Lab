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
  "prize",
  "non-turn",
  "waiting",
  "finished",
] as const;
export type Scenario = (typeof scenarios)[number];
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
  if (scenario === "selection")
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
