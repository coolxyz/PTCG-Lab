import type {
  BattleView,
  CardInfo,
  Command,
  ReplayCheckpoint,
} from "@ptcg/battle-ui";
export type PracticeView = BattleView & {
  aiVersion?: string;
  aiStrategy?: { id: string; version: string; name: string };
};
export type Session = {
  mode: "local-practice";
  cards: CardInfo[];
  engineVersion: string;
  opponents: { id: string; name: string }[];
  presets: { id: string; name: string }[];
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
  aiLevel: "A1" | "H2" | "A2";
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
