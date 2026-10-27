import type { Command, ReplayCheckpoint } from "@ptcg/battle-ui";
import type {
  PracticeAPI,
  PracticeView,
  Session,
  Revision,
  Match,
  Creation,
} from "./types";
import type { PracticeStorage } from "./storage";
import { PracticeAPIError } from "./transport";
export type PracticeState = {
  active: boolean;
  initialized: boolean;
  busy: boolean;
  error: string;
  session: Session | null;
  revisions: Revision[];
  matches: Match[];
  revision: string;
  opponent: string;
  aiLevel: Creation["aiLevel"];
  view: PracticeView | null;
  showing: boolean;
  pending: Command | null;
  creation: Creation | null;
  frame: PracticeView | null;
  seq: number;
  end: number;
  timeline: ReplayCheckpoint[];
  playing: boolean;
  speed: number;
  animate: boolean;
  aiDelay: number;
  paused: boolean;
  coinPlaying: boolean;
};
/** One mode-owned coordinator. Every mutation and timer shares the same lock.
 * Detaching a display cancels scheduling but retains an accepted in-flight result.
 */
export class PracticeController {
  private state: PracticeState = {
    active: false,
    initialized: false,
    busy: false,
    error: "",
    session: null,
    revisions: [],
    matches: [],
    revision: "",
    opponent: "",
    aiLevel: "H2",
    view: null,
    showing: false,
    pending: null,
    creation: null,
    frame: null,
    seq: 0,
    end: 0,
    timeline: [],
    playing: false,
    speed: 1,
    animate: false,
    aiDelay: 1800,
    paused: false,
    coinPlaying: false,
  };
  private lobbyRefreshNeeded = false;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  constructor(
    private api: PracticeAPI,
    private storage: PracticeStorage,
    private id: () => string = () => crypto.randomUUID(),
  ) {}
  snapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private patch(value: Partial<PracticeState>) {
    this.state = { ...this.state, ...value };
    this.schedule();
    for (const listener of this.listeners) listener();
  }
  private schedule() {
    clearTimeout(this.timer);
    this.timer = undefined;
    const s = this.state;
    if (!s.active || !s.showing || s.busy || s.error || s.pending || !s.view)
      return;
    if (s.frame) {
      if (s.playing && s.seq < s.end)
        this.timer = setTimeout(
          () => void this.seek(s.seq + 1, true),
          1000 / s.speed,
        );
      return;
    }
    if (!s.view.done && !s.view.decision && !s.paused && !s.coinPlaying)
      this.timer = setTimeout(() => void this.advance(), s.aiDelay);
  }
  private async run(work: () => Promise<void>, clearError = true) {
    if (this.state.busy) return;
    this.patch({ busy: true, ...(clearError ? { error: "" } : {}) });
    try {
      await work();
    } catch (error) {
      this.patch({
        playing: false,
        error: error instanceof Error ? error.message : "网络中断，请重试",
      });
    } finally {
      this.patch({ busy: false });
      if (
        this.lobbyRefreshNeeded &&
        this.state.active &&
        this.state.initialized
      ) {
        this.lobbyRefreshNeeded = false;
        void this.run(() => this.lobby(), false);
      }
    }
  }
  setActive = (active: boolean) => {
    const entering = active && !this.state.active;
    this.patch({ active, playing: active ? this.state.playing : false });
    if (active && !this.state.initialized) void this.initialize();
    else if (entering) {
      if (this.state.busy) this.lobbyRefreshNeeded = true;
      else void this.run(() => this.lobby(), false);
    }
  };
  private async lobby() {
    const [revisions, matches] = await Promise.all([
      this.api.revisions(),
      this.api.matches(),
    ]);
    const choices = [
      ...(this.state.session?.opponents ?? []).map((o) => o.id),
      ...revisions.filter((r) => r.playable).map((r) => "revision:" + r.id),
    ];
    const preferred = this.storage.takePreferredRevision();
    this.patch({
      revisions,
      matches,
      revision:
        revisions.find((r) => r.playable && r.id === preferred)?.id ||
        revisions.find((r) => r.playable && r.id === this.state.revision)?.id ||
        revisions.find((r) => r.playable && r.id === preferred)?.id ||
        revisions.find((r) => r.playable)?.id ||
        "",
      opponent: choices.includes(this.state.opponent)
        ? this.state.opponent
        : choices[0] || "",
    });
  }
  initialize = () =>
    this.run(async () => {
      this.patch({
        session: await this.api.session(),
        creation: this.storage.creation(),
      });
      await this.lobby();
      const id = this.storage.match();
      if (id) {
        try {
          await this.load(id, true);
        } catch (e) {
          // A transient transport failure must not erase the resume pointer.
          if (e instanceof PracticeAPIError && [403, 404].includes(e.status))
            this.storage.forgetMatch();
          throw e;
        }
      }
      this.patch({ initialized: true });
    });
  private async load(id: string, restoring = false) {
    const view = await this.api.view(id);
    const pending = this.storage.pending(id);
    this.patch({
      view,
      showing: !restoring || !view.done,
      frame: null,
      pending,
      playing: false,
      coinPlaying: false,
      error: pending ? "存在待确认操作，请重试同一命令。" : "",
    });
    this.storage.saveMatch(id);
  }
  open = (id: string) => this.run(() => this.load(id));
  resume = () => {
    if (this.state.view)
      this.patch({
        showing: true,
        error: this.state.pending
          ? "存在待确认操作，请重试同一命令。"
          : this.state.error,
      });
  };
  leave = () => {
    this.patch({ showing: false, playing: false, coinPlaying: false });
    void this.run(() => this.lobby(), false);
  };
  select = (field: "revision" | "opponent" | "aiLevel", value: string) => {
    if (this.state.busy || this.state.creation) return;
    if (field === "aiLevel" && !["A1", "H2", "A2"].includes(value)) return;
    this.patch({ [field]: value });
  };
  preset = (id: string) =>
    this.run(async () => {
      const revision = await this.api.preset(id);
      await this.lobby();
      this.patch({ revision: revision.id });
    });
  create = () =>
    this.run(async () => {
      const s = this.state;
      if (
        !s.initialized ||
        !s.session ||
        (!s.creation && (!s.revision || !s.opponent))
      )
        throw new Error("请选择可对战的卡组版本和 AI 卡组");
      const creation = s.creation ?? {
        requestId: this.id(),
        revisionId: s.revision,
        aiLevel: s.aiLevel,
        ...(s.opponent.startsWith("revision:")
          ? { opponentRevisionId: s.opponent.slice(9) }
          : { opponent: s.opponent }),
      };
      this.storage.saveCreation(creation);
      this.patch({ creation });
      try {
        const view = await this.api.create(creation);
        this.storage.saveMatch(view.matchId);
        this.storage.saveCreation(null);
        this.patch({
          view,
          showing: true,
          creation: null,
          pending: null,
          frame: null,
          playing: false,
          coinPlaying: false,
        });
      } catch (e) {
        if (this.definitive(e)) {
          this.storage.saveCreation(null);
          this.patch({ creation: null });
        }
        throw e;
      }
    });
  private definitive(e: unknown) {
    return (
      e instanceof PracticeAPIError &&
      [400, 403, 404, 409, 422].includes(e.status)
    );
  }
  submit = (choice: Command["choice"]) =>
    this.run(async () => {
      const s = this.state;
      if (
        !s.active ||
        !s.showing ||
        s.frame ||
        s.view?.done ||
        !s.view?.decision ||
        s.pending
      )
        return;
      const command: Command = {
        commandId: "client-" + this.id(),
        expectedStateVersion: s.view.stateVersion,
        decisionId: s.view.decision.id,
        choice: structuredClone(choice),
      };
      await this.send(s.view.matchId, command);
    });
  retry = () =>
    this.run(async () => {
      const s = this.state;
      if (s.view && s.pending) await this.send(s.view.matchId, s.pending);
    });
  private async send(id: string, command: Command) {
    this.storage.savePending(id, command);
    this.patch({ pending: command });
    try {
      const result = await this.api.command(id, command);
      this.storage.savePending(id, null);
      this.patch({ view: result.view, pending: null });
    } catch (e) {
      if (this.definitive(e)) {
        this.storage.savePending(id, null);
        this.patch({ pending: null });
      }
      throw e;
    }
  }
  refresh = () =>
    !this.state.initialized
      ? this.initialize()
      : this.run(async () => {
          if (this.state.showing && this.state.view) {
            const view = await this.api.view(this.state.view.matchId);
            this.patch({
              view,
              error: this.state.pending
                ? "存在待确认操作，请重试同一命令。"
                : "",
            });
          } else {
            this.patch({ session: await this.api.session() });
            await this.lobby();
          }
        });
  pause = () => this.patch({ paused: !this.state.paused });
  delay = (aiDelay: number) => {
    if ([1200, 1800, 3000].includes(aiDelay)) this.patch({ aiDelay });
  };
  setCoinPlaying = (coinPlaying: boolean) => {
    if (this.state.coinPlaying !== coinPlaying) this.patch({ coinPlaying });
  };
  advance = () =>
    this.run(async () => {
      const s = this.state;
      if (
        !s.active ||
        !s.showing ||
        !s.view ||
        s.view.done ||
        s.view.decision ||
        s.pending ||
        s.frame ||
        s.coinPlaying
      )
        return;
      this.patch({ view: await this.api.advance(s.view.matchId) });
    });
  resign = () =>
    this.run(async () => {
      const s = this.state;
      if (!s.view || s.view.done || s.pending || s.frame) return;
      this.patch({
        view: await this.api.resign(s.view.matchId, s.view.stateVersion),
      });
    });
  openReplay = (id: string) =>
    this.run(async () => {
      await this.load(id);
      await this.replayFrame(0);
    });
  seek = (seq: number, automatic = false) =>
    this.run(() => this.replayFrame(seq, automatic));
  private async replayFrame(seq: number, automatic = false) {
    const s = this.state;
    if (!s.view || s.pending) return;
    if (!automatic) this.patch({ playing: false });
    if (!s.frame) {
      const index = await this.api.timeline(s.view.matchId);
      this.patch({ end: index.lastSeq, timeline: index.checkpoints });
    }
    const frames = await this.api.replay(s.view.matchId, seq);
    const entry = frames.frames[0];
    if (!entry || entry.seq !== seq)
      throw new Error("未找到该回放步骤，请刷新后重试");
    this.patch({
      frame: { ...entry.view, events: entry.event ? [entry.event] : [] },
      seq,
      animate: automatic,
      playing: seq >= this.state.end ? false : this.state.playing,
    });
  }
  play = (playing: boolean) => this.patch({ playing });
  speed = (speed: number) => {
    if ([1, 2, 4].includes(speed)) this.patch({ speed });
  };
  closeReplay = () =>
    this.patch({ frame: null, playing: false, coinPlaying: false });
}
