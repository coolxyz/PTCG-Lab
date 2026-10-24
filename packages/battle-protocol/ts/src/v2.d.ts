// Generated from v2.schema.json. Do not edit. Runtime validation is required.

/**
 * P2-D strict v2 development contract; explicit visible presentation fields, no private recovery payload.
 */
export type BattleProtocolV2 = Command | DeckSubmission | MatchSnapshot | Capabilities | ProtocolError | ReplayPage;
export type ProtocolVersion = "2.0";
export type Identifier = string;
export type Seat = "PLAYER1" | "PLAYER2";
export type Decision = OptionsDecision | SelectionDecision;
export type ErrorCode =
  | "INVALID_JSON"
  | "PAYLOAD_TOO_LARGE"
  | "INVALID_PAYLOAD"
  | "UNSUPPORTED_PROTOCOL"
  | "NOT_ACTOR"
  | "STALE_DECISION"
  | "MATCH_VERSION_CONFLICT"
  | "CONTROL_LOST"
  | "INVALID_OPTION"
  | "INVALID_SELECTION"
  | "IDEMPOTENCY_CONFLICT"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "RESYNC_REQUIRED"
  | "RULE_RELEASE_UNAVAILABLE"
  | "CAPACITY_EXCEEDED"
  | "INTERNAL_ERROR";

export interface Command {
  protocolVersion: ProtocolVersion;
  commandId: Identifier;
  expectedMatchSeq: number;
  expectedStateVersion: number;
  controllerEpoch: number;
  decisionId: Identifier;
  choice: OptionChoice | SelectionChoice;
}
export interface OptionChoice {
  optionId: Identifier;
}
export interface SelectionChoice {
  /**
   * @maxItems 60
   */
  selectedRefs: Identifier[];
}
export interface DeckSubmission {
  protocolVersion: ProtocolVersion;
  requestId: Identifier;
  ruleReleaseId: Identifier;
  /**
   * @minItems 1
   * @maxItems 60
   */
  entries: [DeckEntry, ...DeckEntry[]];
  name?: string;
}
export interface DeckEntry {
  printingId: Identifier;
  quantity: number;
}
export interface MatchSnapshot {
  protocolVersion: ProtocolVersion;
  matchId: Identifier;
  ruleReleaseId: Identifier;
  viewerSeat: Seat;
  matchSeq: number;
  stateVersion: number;
  controllerEpoch: number;
  streamCursor: Identifier;
  status: "playing" | "finished" | "aborted";
  phase: string;
  done: boolean;
  winner: Seat | null;
  decision: Decision | null;
  observation: Observation;
  /**
   * @maxItems 256
   */
  events: VisibleEvent[];
  startingPlayer?: Seat | null;
  catalogVersion?: string;
  /**
   * @maxItems 256
   */
  setupEvents?: SetupEvent[];
  /**
   * @maxItems 256
   */
  publicReveals?: PublicReveal[];
  /**
   * @maxItems 256
   */
  visualEffects?: VisualEffect[];
  result?: ResultExplanation | null;
}
export interface OptionsDecision {
  id: Identifier;
  actor: Seat;
  kind: "options";
  /**
   * @minItems 1
   * @maxItems 2048
   */
  options: [Option, ...Option[]];
  instruction?: string;
}
export interface Option {
  id: Identifier;
  actionType: string;
  label?: string;
  sourceRef?: string | null;
  targetRef?: string | null;
  activeRef?: Identifier;
  source?: string | null;
  sourceId?: string | null;
  sourcePosition?: string | null;
  target?: string | null;
  targetId?: string | null;
  targetPosition?: string | null;
  active_pokemon?: string | null;
  active_pokemonId?: string | null;
  active_pokemonPosition?: string | null;
  active_pokemonRef?: string | null;
  ability?: string | null;
  attack?: {
    name: string;
    damage: number;
  };
  position?: string | null;
  kind?: string | null;
  description?: string | null;
  sourceIndex?: number | null;
  targetIndex?: number | null;
  active_pokemonIndex?: number | null;
  /**
   * @maxItems 60
   */
  candidates?: string[];
  /**
   * @maxItems 60
   */
  chosen?: string[];
  value?: string | number | boolean;
}
export interface SelectionDecision {
  id: Identifier;
  actor: Seat;
  kind: "selection";
  min: number;
  max: number;
  hidden: boolean;
  indexed: boolean;
  /**
   * @maxItems 60
   */
  candidates: Candidate[];
  instruction?: string;
  deckInspection?: DeckInspection;
  source?: string | null;
  sourceId?: string | null;
  /**
   * @maxItems 20
   */
  zones?: string[];
}
export interface Candidate {
  ref: Identifier;
  card?: CardIdentity;
  boardRef?: Identifier;
  side?: "self" | "opponent";
  zone?: "active" | "bench" | "discard" | "hand" | "lostZone" | "stadium";
  index?: number;
  position?: string;
  revealedPrizeIndex?: number;
  revealedPrizeSide?: "self" | "opponent";
}
export interface CardIdentity {
  cardId: Identifier;
  printingId?: Identifier;
  name: string;
  presentation?: VisibleCardPresentation;
}
export interface VisibleCardPresentation {
  name: string;
  id?: string;
  kind?: "card" | "attack";
  ref?: Identifier;
  prizeIndex?: number;
  set_name?: string;
  number?: string;
  superType?: string;
  trainerType?: string;
  cardType?: string;
  pokemonType?: string;
  energyType?: string;
  stage?: string;
  specialCondition?: string;
  effect?: string;
  text?: string;
  /**
   * @maxItems 120
   */
  attachment?: string[];
  /**
   * @maxItems 120
   */
  tool?: string[];
  /**
   * @maxItems 120
   */
  energy?: string[];
  /**
   * @maxItems 120
   */
  evolved?: string[];
  /**
   * @maxItems 120
   */
  effectiveTypes?: string[];
  /**
   * @maxItems 120
   */
  evolveFrom?: string[];
  /**
   * @maxItems 120
   */
  provides?: string[];
  /**
   * @maxItems 120
   */
  resistance?: string[];
  /**
   * @maxItems 120
   */
  weakness?: string[];
  /**
   * @maxItems 120
   */
  retreatCost?: string[];
  /**
   * @maxItems 120
   */
  limitedAttacks?: string[];
  /**
   * @maxItems 120
   */
  cost?: string[];
  abilityUsed?: boolean;
  firstTurnPlayed?: boolean;
  retreatBlocked?: boolean;
  attackBlocked?: boolean;
  poisoned?: boolean;
  burned?: boolean;
  abilitiesSuppressed?: boolean;
  maximumHp?: number;
  damage_counters?: number;
  prize?: number;
  poisonDamage?: number;
  attackDamageReduction?: number;
  preventAttackDamageAtMost?: number;
  attackPowerReduction?: number;
  attackPowerBonus?: number;
  retaliationCounters?: number;
  damage?: number;
  hp?: number | string;
  /**
   * @maxItems 30
   */
  attacks?: VisibleAttack[];
  /**
   * @maxItems 30
   */
  abilities?: VisibleAbility[];
  recovery?: RecoveryEffect;
  attackProtection?: AttackProtection;
}
export interface VisibleAttack {
  name: string;
  damage: number;
  /**
   * @maxItems 120
   */
  cost: string[];
  text?: string;
}
export interface VisibleAbility {
  name: string;
  text: string;
}
export interface RecoveryEffect {
  amount: number;
  cure: boolean;
}
export interface AttackProtection {
  damage?: boolean;
  effects?: boolean;
  source?: string;
}
export interface DeckInspection {
  owner: "self" | "opponent";
  ordered: false;
  total: number;
  /**
   * @maxItems 60
   */
  cards: {
    card: CardIdentity;
    count: number;
  }[];
}
export interface Observation {
  turn: Seat | null;
  turnNumber: number;
  self: OwnSide;
  opponent: OpponentSide;
  /**
   * @maxItems 1
   */
  stadium: CardState[];
  viewer?: "player1" | "player2";
  timestep?: number;
  isChoosingCard?: boolean;
  terminationReason?: string | null;
  /**
   * @maxItems 256
   */
  autoEvents?: string[];
}
export interface OwnSide {
  /**
   * @maxItems 1
   */
  active: CardState[];
  /**
   * @maxItems 60
   */
  bench: CardState[];
  /**
   * @maxItems 60
   */
  discard: CardIdentity[];
  /**
   * @maxItems 60
   */
  lostZone: CardIdentity[];
  handCount: number;
  deckCount: number;
  prizeCount: number;
  /**
   * @maxItems 60
   */
  faceUpPrizes: VisiblePrize[];
  benchCapacity: number;
  /**
   * @maxItems 60
   */
  hand: CardIdentity[];
  id?: "player1" | "player2";
  turnFlags?: TurnFlags;
}
export interface CardState {
  ref: Identifier;
  card: CardIdentity;
  damage?: number;
  hp?: number;
  maximumHp?: number;
  /**
   * @maxItems 120
   */
  energy?: string[];
  /**
   * @maxItems 60
   */
  tools?: CardIdentity[];
  /**
   * @maxItems 60
   */
  evolved?: CardIdentity[];
  /**
   * @maxItems 5
   */
  conditions?: ("ASLEEP" | "PARALYZED" | "CONFUSED" | "POISONED" | "BURNED")[];
  /**
   * @maxItems 5
   */
  flags?: ("abilityUsed" | "abilitiesSuppressed" | "retreatBlocked" | "attackBlocked" | "firstTurnPlayed")[];
}
export interface VisiblePrize {
  index: number;
  card: CardIdentity;
}
export interface TurnFlags {
  firstPersonalTurn: boolean;
  energyPlayed: boolean;
  supporterPlayed: boolean;
  itemsBlocked: boolean;
  retreated: boolean;
  attacksBlocked?: boolean;
}
export interface OpponentSide {
  /**
   * @maxItems 1
   */
  active: CardState[];
  /**
   * @maxItems 60
   */
  bench: CardState[];
  /**
   * @maxItems 60
   */
  discard: CardIdentity[];
  /**
   * @maxItems 60
   */
  lostZone: CardIdentity[];
  handCount: number;
  deckCount: number;
  prizeCount: number;
  /**
   * @maxItems 60
   */
  faceUpPrizes: VisiblePrize[];
  benchCapacity: number;
  hand: null;
  id?: "player1" | "player2";
  turnFlags?: TurnFlags;
}
export interface VisibleEvent {
  seq: number;
  actor: Seat | null;
  kind: "action" | "setup" | "reveal" | "coin" | "result";
  text: string;
  /**
   * @maxItems 60
   */
  cards?: CardIdentity[];
  /**
   * @maxItems 200
   */
  coins?: ("heads" | "tails")[];
}
export interface SetupEvent {
  kind: "order_draw" | "mulligan";
  winner?: Seat;
  hands?: RevealedMulliganHands;
}
export interface RevealedMulliganHands {
  /**
   * @maxItems 7
   */
  PLAYER1?: VisibleCardPresentation[];
  /**
   * @maxItems 7
   */
  PLAYER2?: VisibleCardPresentation[];
}
export interface PublicReveal {
  kind: string;
  actor: Seat;
  /**
   * @maxItems 60
   */
  cards: VisibleCardPresentation[];
}
export interface VisualEffect {
  kind: "move" | "count" | "hp" | "turn";
  side: "self" | "opponent";
  zone: string;
  fromZone?: string;
  cardName?: string | null;
  label?: string;
  index?: number;
  before: number;
  after: number;
}
export interface ResultExplanation {
  reason: string;
  /**
   * @maxItems 10
   */
  conditions: string[];
  outcome?: "draw";
}
export interface Capabilities {
  protocolVersion: ProtocolVersion;
  /**
   * @minItems 1
   * @maxItems 8
   */
  supportedProtocolVersions: [ProtocolVersion, ...ProtocolVersion[]];
  /**
   * @maxItems 16
   */
  ruleReleaseIds: Identifier[];
  resources: ResourceVersions;
  acceptingMatches: boolean;
  maxCommandBytes: number;
  /**
   * @maxItems 4
   */
  features: ("invite" | "duel" | "private-replay" | "reconnect")[];
}
export interface ResourceVersions {
  displayCatalogVersion: Identifier;
  assetManifestVersion: Identifier;
}
export interface ProtocolError {
  protocolVersion: ProtocolVersion;
  code: ErrorCode;
  requestId?: Identifier;
  retryable: boolean;
}
export interface ReplayPage {
  protocolVersion: ProtocolVersion;
  replaySchemaVersion: "visible-replay-v2";
  matchId: Identifier;
  ruleReleaseId: Identifier;
  viewerSeat: Seat;
  /**
   * @maxItems 32
   */
  frames: ReplayFrame[];
  nextCursor: Identifier | null;
}
export interface ReplayFrame {
  frameSeq: number;
  matchSeq: number;
  stateVersion: number;
  status: "playing" | "finished" | "aborted";
  phase: string;
  done: boolean;
  winner: Seat | null;
  observation: Observation;
  /**
   * @maxItems 256
   */
  events: VisibleEvent[];
  startingPlayer?: Seat | null;
  catalogVersion?: string;
  /**
   * @maxItems 256
   */
  setupEvents?: SetupEvent[];
  /**
   * @maxItems 256
   */
  publicReveals?: PublicReveal[];
  /**
   * @maxItems 256
   */
  visualEffects?: VisualEffect[];
  result?: ResultExplanation | null;
}
