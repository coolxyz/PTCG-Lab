import type {
  CardIdentity,
  CardState,
  MatchSnapshot,
  OwnSide,
  OpponentSide,
  ReplayFrame,
  ReplayPage,
} from "@ptcg/battle-protocol";
import type {
  BattleView,
  Decision,
  EngineCard,
  Option,
  Side,
} from "@ptcg/battle-ui";

/** Display adapter for an already validated, member-scoped v2 DTO.
 * This is not an authentication boundary or a JSON validator. P5 supplies those.
 * A detached result prevents display state from mutating a cached snapshot.
 */
function card(value: CardIdentity): EngineCard {
  const p = value.presentation;
  return {
    ...p,
    name: p?.name ?? value.name,
    id: p?.id ?? value.cardId,
    hp:
      p?.hp === undefined || p.hp === "" || !Number.isFinite(Number(p.hp))
        ? undefined
        : Number(p.hp),
    attacks: p?.attacks?.map((a) => ({ ...a, text: a.text ?? "" })),
  };
}
function board(value: CardState): EngineCard {
  const out = card(value.card);
  for (const key of ["damage", "hp", "maximumHp", "energy"] as const) {
    if (value[key] !== undefined) Object.assign(out, { [key]: value[key] });
  }
  if (value.tools) out.tool = value.tools.map((c) => c.name);
  if (value.evolved) out.evolved = value.evolved.map((c) => c.name);
  if (value.conditions) {
    out.poisoned = value.conditions.includes("POISONED");
    out.burned = value.conditions.includes("BURNED");
    out.specialCondition = value.conditions
      .filter((c) => c !== "POISONED" && c !== "BURNED")
      .join(", ");
  }
  for (const flag of value.flags ?? []) Object.assign(out, { [flag]: true });
  return { ...out, ref: value.ref };
}
function side(value: OwnSide | OpponentSide): Side {
  return {
    active: value.active.map(board),
    bench: value.bench.map(board),
    discard: value.discard.map(card),
    lost_zone: value.lostZone.map(card),
    hand: value.hand?.map(card) ?? null,
    hand_count: value.handCount,
    deck_count: value.deckCount,
    prize_count: value.prizeCount,
    faceUpPrizes: value.faceUpPrizes.map((p) => ({
      ...card(p.card),
      prizeIndex: p.index,
    })),
    benchCapacity: value.benchCapacity,
    turnFlags: value.turnFlags,
  };
}
function decision(
  value: MatchSnapshot["decision"],
  seat: MatchSnapshot["viewerSeat"],
): Decision | null {
  // Even a misrouted actor DTO must not enable another seat's controls.
  if (!value || value.actor !== seat) return null;
  if (value.kind === "options")
    return {
      id: value.id,
      actor: value.actor,
      kind: "options",
      min: 0,
      max: 0,
      hidden: false,
      candidates: [],
      options: value.options.map(
        (o) =>
          Object.fromEntries(
            Object.entries(o).filter(([, v]) => v !== null),
          ) as Option,
      ),
    };
  return {
    ...value,
    source: value.source ?? undefined,
    options: [],
    candidates: value.candidates.map((c) => ({
      ...c,
      card: c.card ? card(c.card) : undefined,
    })),
    deckInspection: value.deckInspection
      ? {
          ...value.deckInspection,
          cards: value.deckInspection.cards.map((row) => ({
            ...row,
            card: card(row.card),
          })),
        }
      : undefined,
  };
}
function display(
  value: MatchSnapshot | ReplayFrame,
  matchId: string,
): BattleView {
  return {
    matchId,
    matchSeq: value.matchSeq,
    stateVersion: value.stateVersion,
    status: value.status,
    phase: value.phase,
    done: value.done,
    winner: value.winner,
    result: value.result,
    decision: null,
    observation: {
      self: side(value.observation.self),
      opponent: side(value.observation.opponent),
      turn: value.observation.turn,
      turn_number: value.observation.turnNumber,
      stadium: value.observation.stadium.map(board),
      termination_reason: value.observation.terminationReason ?? null,
    },
    events: value.events.map((e) => ({
      ...e,
      actor: e.actor ?? "system",
      effects: e.seq === value.matchSeq ? value.visualEffects : undefined,
    })),
    visualEffects: value.visualEffects,
    setupEvents: value.setupEvents,
    publicReveals: value.publicReveals,
  };
}
export function snapshotView(snapshot: MatchSnapshot): BattleView {
  const value = structuredClone(snapshot);
  return {
    ...display(value, value.matchId),
    decision: decision(value.decision, value.viewerSeat),
  };
}
export function replayView(page: ReplayPage, frameIndex: number): BattleView {
  const frame = page.frames[frameIndex];
  if (!frame) throw new RangeError("Replay frame does not exist");
  return display(structuredClone(frame), page.matchId); // No live decision is ever copied.
}
