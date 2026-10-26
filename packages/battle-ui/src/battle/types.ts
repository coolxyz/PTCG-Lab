import type { ChineseDetails } from "../CardDescription";

export type CardInfo = {
  aliases?: string[];
  attacks?: { name: string }[];
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
  attackProtection?: { damage?: boolean; effects?: boolean; source?: string };
  attacks?: { name: string; damage: number; text: string }[];
  abilities?: { name: string; text: string }[];
  kind?: string;
  trainerType?: string;
  superType?: string;
  damage?: number;
};
export type Side = {
  turnFlags?: { itemsBlocked?: boolean; attacksBlocked?: boolean };
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
  sourceId?: string;
  targetId?: string;
  active_pokemonId?: string;
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
  actor?: PlayerSeat;
  kind: string;
  min: number;
  max: number;
  hidden: boolean;
  indexed?: boolean;
  source?: string;
  instruction?: string;
  deckInspection?: {
    owner: "self" | "opponent";
    total: number;
    ordered: false;
    cards: { card: EngineCard; count: number }[];
  };
  candidates: {
    ref: string;
    boardRef?: string;
    card?: EngineCard;
    side?: string;
    position?: string;
    index?: number;
    revealedPrizeIndex?: number;
    revealedPrizeSide?: "self" | "opponent";
  }[];
  options: Option[];
};
export type BattleView = {
  result?: { reason: string; conditions: string[]; outcome?: string } | null;
  matchId: string;
  stateVersion: number;
  matchSeq?: number;
  status: string;
  done: boolean;
  winner: string | null;
  phase: string;
  decision: Decision | null;
  observation: {
    turn: string | null;
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
    turn?: number;
    kind?: string;
    cardGroups?: { label: string; cards: EngineCard[] }[];
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
  visualEffects?: NonNullable<
    NonNullable<BattleView["events"]>[number]["effects"]
  >;
  setupEvents?: any[];
  publicReveals?: any[];
};
export type Command = {
  commandId: string;
  expectedStateVersion: number;
  decisionId: string;
  choice: { optionId?: string; selectedRefs?: string[] };
};
export type ReplayCheckpoint = {
  seq: number;
  turn: number;
  player: string | null;
  phase: string;
};

export type PlayerSeat = "PLAYER1" | "PLAYER2";
