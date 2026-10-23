// Generated from v2.schema.json. Do not edit. Runtime validation is required.

/**
 * P2-A wire contract. Private engine records and seeds are deliberately not representable.
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
  label: string;
  sourceRef?: Identifier;
  targetRef?: Identifier;
  activeRef?: Identifier;
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
}
export interface Candidate {
  ref: Identifier;
  card?: CardIdentity;
  boardRef?: Identifier;
  side?: "self" | "opponent";
  zone?: "active" | "bench" | "discard" | "hand" | "lostZone" | "stadium";
  index?: number;
}
export interface CardIdentity {
  cardId: Identifier;
  printingId?: Identifier;
  name: string;
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
}
