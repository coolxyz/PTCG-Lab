import type {
  BattleView,
  CardInfo,
  Command,
  ReplayCheckpoint,
} from "@ptcg/battle-ui";
export type AIStrategy = { id: string; version: string; name: string };
const AI_LEVELS = ["A1", "H2", "A2", "LLM"] as const;
export type AILevel = (typeof AI_LEVELS)[number];

export function isAILevel(value: unknown): value is AILevel {
  return AI_LEVELS.some((level) => level === value);
}

export type PracticeView = BattleView & {
  aiVersion?: string;
  aiStrategy?: AIStrategy;
};
export type Session = {
  mode: "local-practice";
  cards: CardInfo[];
  engineVersion: string;
  opponents: { id: string; name: string }[];
  presets: { id: string; name: string }[];
  llmOpponent?: AIStrategy | null;
};
export type Revision = {
  id: string;
  name: string;
  number: number;
  playable: boolean;
  reason?: string;
};
export type Match = {
  id: string;
  opponent: string;
  status: string;
  seq: number;
};
export type Creation = {
  requestId: string;
  revisionId: string;
  aiLevel: AILevel;
  opponent?: string;
  opponentRevisionId?: string;
};
export interface PracticeAPI {
  session(): Promise<Session>;
  revisions(): Promise<Revision[]>;
  matches(): Promise<Match[]>;
  preset(id: string): Promise<Revision>;
  create(body: Creation): Promise<PracticeView>;
  view(id: string): Promise<PracticeView>;
  command(id: string, body: Command): Promise<{ view: PracticeView }>;
  advance(id: string): Promise<PracticeView>;
  resign(id: string, version: number): Promise<PracticeView>;
  timeline(
    id: string,
  ): Promise<{ lastSeq: number; checkpoints: ReplayCheckpoint[] }>;
  replay(
    id: string,
    seq: number,
  ): Promise<{
    frames: {
      seq: number;
      view: PracticeView;
      event?: NonNullable<BattleView["events"]>[number];
    }[];
  }>;
}
