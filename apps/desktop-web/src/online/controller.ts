import type {
  MatchSnapshot,
  ReplayPage,
  ReplayFrame,
} from "@ptcg/battle-protocol";
import type { CardInfo, Command } from "@ptcg/battle-ui";
import { OnlineError, type OnlineAPI } from "./transport";
import type { OnlineStorage, Remembered } from "./storage";
export type Room = {
  roomId: string;
  roomVersion: number;
  inviteCode: string;
  ruleReleaseId: string;
  viewerSeat: string;
  status: string;
  members: { seat: string; ready: boolean }[];
  matchId: string | null;
};
type Status = {
  identity: { userId: string } | null;
  reauthenticationRequired: boolean;
  pendingMatches: string[];
  recovery: {
    commands: { matchId: string; commandId: string }[];
    controls: { matchId: string }[];
  };
};
type Caps = {
  ruleReleaseIds: string[];
  acceptingMatches: boolean;
  resources: { displayCatalogVersion: string; assetManifestVersion: string };
};
export const errorText = (error: unknown) => {
  const code = error instanceof OnlineError ? error.code : "LOCAL_STATE_FAILED";
  const names: Record<string, string> = {
    LOCAL_AUTH_REQUIRED: "本机窗口授权已失效，请从本地启动器重新打开。",
    REAUTHENTICATION_REQUIRED:
      "身份需要重新认证。游客身份无法自动找回，请保留本地凭据和待确认记录。",
    LOCAL_CONTROL_REQUIRED: "另一个窗口已接管，请重新取得操作权。",
    CONTROL_LOST: "操作权已变化，请刷新后重新接管。",
    PROFILE_BUSY: "后台正在处理请求，请稍后重试。",
    CONNECTION_UNKNOWN:
      "连接中断，操作结果可能尚未确认。请恢复原请求，勿重复创建。",
    NETWORK_UNAVAILABLE: "远程连接中断，请检查连接并恢复原请求。",
    OUTCOME_UNKNOWN: "服务器尚未确认操作结果，请继续恢复，不能提交新操作。",
    RULE_RELEASE_UNAVAILABLE: "服务器没有所选规则版本。",
    STREAM_UNAVAILABLE: "推送已断开，请重新连接。",
  };
  return names[code] || `操作未完成（${code}），请刷新状态或重试原请求。`;
};
export type State = {
  profiles: { id: string; origin: string }[];
  profile: string;
  status: Status | null;
  caps: Caps | null;
  rule: string;
  room: Room | null;
  view: MatchSnapshot | null;
  lease: string;
  cards: Record<string, CardInfo>;
  missing: boolean;
  busy: boolean;
  error: string;
  showing: boolean;
  pending: boolean;
  remembered: Remembered;
  replay: ReplayPage | null;
  frame: number;
  nextCursor: string | null;
  playing: boolean;
  speed: number;
  connected: boolean;
};
export class OnlineController {
  private state: State = {
    profiles: [],
    profile: "",
    status: null,
    caps: null,
    rule: "",
    room: null,
    view: null,
    lease: "",
    cards: {},
    missing: true,
    busy: false,
    error: "",
    showing: false,
    pending: false,
    remembered: { userId: "" },
    replay: null,
    frame: 0,
    nextCursor: null,
    playing: false,
    speed: 1,
    connected: false,
  };
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private disposed = false;
  private initializingTries = 0;
  constructor(
    private api: OnlineAPI,
    private storage: OnlineStorage,
    private id = () => crypto.randomUUID(),
  ) {}
  snapshot = () => this.state;
  subscribe = (f: () => void) => {
    this.listeners.add(f);
    return () => {
      this.listeners.delete(f);
    };
  };
  private patch(s: Partial<State>) {
    this.state = { ...this.state, ...s };
    this.listeners.forEach((f) => f());
  }
  private save(s: Partial<Remembered>) {
    const next = { ...this.state.remembered, ...s };
    this.storage.write(this.state.profile, next);
    this.patch({ remembered: next });
  }
  private call<T>(op: string, body: object = {}) {
    return this.api.call<T>(this.state.profile, op, body);
  }
  private async run(fn: () => Promise<void>) {
    if (this.state.busy || this.disposed) return;
    clearTimeout(this.timer);
    this.patch({ busy: true, error: "" });
    try {
      await fn();
    } catch (e) {
      this.patch({ error: errorText(e), playing: false });
      try {
        await this.status();
      } catch {
        /* Keep unknown result visibly blocked. */
      }
    } finally {
      this.patch({ busy: false });
      this.schedule();
    }
  }
  private schedule() {
    clearTimeout(this.timer);
    if (this.disposed || this.state.busy || this.state.error) return;
    const s = this.state;
    if (s.replay && s.playing) {
      this.timer = setTimeout(() => {
        if (this.state.frame + 1 < this.state.replay!.frames.length) {
          this.patch({ frame: this.state.frame + 1 });
          this.schedule();
        } else this.patch({ playing: false });
      }, 1000 / s.speed);
      return;
    }
    if (
      (s.showing && s.view && !s.view.done) ||
      (!s.showing &&
        s.room &&
        ["waiting", "initializing", "initialized"].includes(s.room.status))
    )
      this.timer = setTimeout(
        () =>
          void this.run(async () => {
            if (this.state.showing) {
              const f = await this.call<{
                event: { snapshot: MatchSnapshot } | null;
                done: boolean;
                error: { code: string } | null;
              }>("events", { matchId: this.state.view!.matchId });
              if (f.event) this.accept(f.event.snapshot);
              if (f.done)
                throw new OnlineError(f.error?.code || "STREAM_UNAVAILABLE");
            } else await this.room();
          }),
        s.showing ? 600 : 1500,
      );
  }
  private accept(view: MatchSnapshot) {
    const old = this.state.view;
    if (
      old &&
      old.matchId === view.matchId &&
      (view.matchSeq < old.matchSeq ||
        view.controllerEpoch < old.controllerEpoch)
    )
      return;
    this.patch({
      view,
      ...(old &&
      old.matchId === view.matchId &&
      old.controllerEpoch !== view.controllerEpoch
        ? { lease: "" }
        : {}),
    });
  }
  async start() {
    await this.run(async () => {
      const profiles = await this.api.profiles();
      this.patch({ profiles });
      const selected = this.storage.selected();
      await this.selectInner(
        profiles.some((p) => p.id === selected)
          ? selected
          : profiles[0]?.id || "",
      );
    });
  }
  select = (profile: string) => this.run(() => this.selectInner(profile));
  private async selectInner(profile: string) {
    if (!this.state.profiles.some((p) => p.id === profile)) return;
    if (this.state.view)
      await this.call("unwatch", { matchId: this.state.view.matchId }).catch(
        () => {},
      );
    this.storage.selected(profile);
    this.patch({
      profile,
      status: null,
      caps: null,
      rule: "",
      room: null,
      view: null,
      lease: "",
      cards: {},
      replay: null,
      showing: false,
      pending: false,
      connected: false,
      remembered: { userId: "" },
    });
    await this.status();
    const caps = await this.call<Caps>("capabilities");
    const rememberedRule = this.state.remembered.rule;
    const rule =
      rememberedRule && caps.ruleReleaseIds.includes(rememberedRule)
        ? rememberedRule
        : caps.ruleReleaseIds[0] || "";
    if (rule !== rememberedRule) this.save({ rule, revision: undefined });
    this.patch({ caps, rule, connected: true });
    try {
      const catalog = await this.api.catalog(
        caps.resources.displayCatalogVersion,
        caps.resources.assetManifestVersion,
      );
      this.patch({
        cards: Object.fromEntries(
          catalog.cards.flatMap((c) => [
            [c.engineId, c],
            [c.englishName, c],
          ]),
        ),
        missing: !!catalog.missing || !catalog.cards.length,
      });
    } catch {
      this.patch({ missing: true });
    }
    if (this.state.remembered.roomId) await this.room();
    else if (this.state.remembered.matchId)
      await this.openInner(this.state.remembered.matchId);
  }
  private async status() {
    const status = await this.call<Status>("status");
    this.patch({ status, pending: !!status.pendingMatches.length });
    if (status.identity) {
      const remembered = this.storage.read(
        this.state.profile,
        status.identity.userId,
      );
      this.patch({ remembered });
    }
  }
  guest = (name: string) =>
    this.run(async () => {
      await this.call("guest", { name, deviceName: "PTCG-Lab 本地窗口" });
      await this.status();
    });
  rule = (rule: string) => {
    this.save({ rule, revision: undefined });
    this.patch({ rule });
  };
  private async job(operation: string, body: Record<string, unknown>) {
    if (this.state.remembered.job)
      throw new OnlineError("PENDING_LOBBY_REQUEST");
    this.save({ job: { operation, body } });
    await this.executeJob(true);
  }
  private async executeJob(fresh = false) {
    const job = this.state.remembered.job;
    if (!job) return;
    let result: Room & { deckRevision: string };
    try {
      result = await this.call<Room & { deckRevision: string }>(
        job.operation,
        job.body,
      );
    } catch (error) {
      if (
        fresh &&
        error instanceof OnlineError &&
        !error.retryable &&
        [
          "MATCH_VERSION_CONFLICT",
          "INVALID_PAYLOAD",
          "RULE_RELEASE_UNAVAILABLE",
        ].includes(error.code)
      )
        this.save({ job: undefined });
      throw error;
    }
    // Preserve the original request until both result metadata and clearance are durable.
    const next: Partial<Remembered> =
      job.operation === "deck"
        ? {
            revision: result.deckRevision,
            rule: job.body.ruleReleaseId as string,
          }
        : job.operation === "leave-room"
          ? { roomId: undefined, matchId: undefined }
          : { roomId: result.roomId, matchId: result.matchId || undefined };
    this.save({ ...next, job: undefined });
    if (job.operation !== "deck") {
      this.patch({ room: job.operation === "leave-room" ? null : result });
      if (result.matchId) await this.enterRoomMatch(result);
    }
  }
  retryLobby = () => this.run(() => this.executeJob());
  deck = (text: string) =>
    this.run(async () => {
      let entries: unknown;
      try {
        entries = JSON.parse(text);
      } catch {
        throw new OnlineError("INVALID_DECK_JSON");
      }
      if (!Array.isArray(entries)) throw new OnlineError("INVALID_DECK_JSON");
      await this.job("deck", {
        entries,
        ruleReleaseId: this.state.rule,
        requestId: this.id(),
      });
    });
  create = () =>
    this.run(() =>
      this.job("create-room", {
        deckRevision: this.state.remembered.revision,
        ruleReleaseId: this.state.rule,
        requestId: this.id(),
      }),
    );
  join = (inviteCode: string) =>
    this.run(() =>
      this.job("join-room", {
        deckRevision: this.state.remembered.revision,
        inviteCode: inviteCode.trim().toUpperCase(),
        requestId: this.id(),
      }),
    );
  ready = () =>
    this.run(async () => {
      const r = this.state.room!;
      const ready = !r.members.find((m) => m.seat === r.viewerSeat)?.ready;
      await this.job("ready", {
        roomId: r.roomId,
        expectedRoomVersion: r.roomVersion,
        ready,
        requestId: this.id(),
      });
    });
  leaveRoom = () =>
    this.run(async () => {
      const r = this.state.room!;
      await this.job("leave-room", {
        roomId: r.roomId,
        expectedRoomVersion: r.roomVersion,
        requestId: this.id(),
      });
    });
  private async room() {
    const id = this.state.remembered.roomId;
    if (!id) return;
    const room = await this.call<Room>("room", { roomId: id });
    this.patch({ room });
    if (room.matchId) {
      this.save({ matchId: room.matchId });
      await this.enterRoomMatch(room);
    }
  }
  private async enterRoomMatch(room: Room) {
    if (
      !room.matchId ||
      room.status === "initializing" ||
      room.status === "waiting"
    )
      return;
    try {
      await this.openInner(room.matchId);
      this.initializingTries = 0;
    } catch (error) {
      // A room can be initialized just before its authoritative match is promoted.
      if (
        room.status === "initialized" &&
        error instanceof OnlineError &&
        error.code === "FORBIDDEN" &&
        ++this.initializingTries < 40
      )
        return;
      throw error;
    }
  }
  private async openInner(matchId: string) {
    const view = await this.call<MatchSnapshot>("snapshot", { matchId });
    this.save({ matchId, roomId: undefined });
    this.accept(view);
    this.patch({ showing: true, replay: null, lease: "" });
    if (!view.done) await this.watch(matchId);
  }
  private async watch(matchId: string) {
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        await this.call("watch", { matchId });
        return;
      } catch (error) {
        if (
          !(error instanceof OnlineError) ||
          error.code !== "STREAM_STOPPING" ||
          attempt === 9
        )
          throw error;
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    }
  }
  dismissRoom = () =>
    this.run(async () => {
      this.save({ roomId: undefined, matchId: undefined });
      this.patch({ room: null });
    });
  open = (id: string) => this.run(() => this.openInner(id));
  refresh = () =>
    this.run(async () => {
      if (!this.state.caps) {
        await this.selectInner(this.state.profile);
        return;
      }
      await this.status();
      if (this.state.view) {
        this.accept(
          await this.call("snapshot", { matchId: this.state.view.matchId }),
        );
        if (!this.state.view.done) await this.watch(this.state.view.matchId);
      } else await this.room();
    });
  control = () =>
    this.run(async () => {
      const v = this.state.view!;
      const pending = this.state.status?.recovery.controls.some(
        (p) => p.matchId === v.matchId,
      );
      const value = await this.call<{ lease: string; snapshot: MatchSnapshot }>(
        pending ? "recover-control" : "control",
        {
          matchId: v.matchId,
          ...(pending ? {} : { expectedControllerEpoch: v.controllerEpoch }),
        },
      );
      this.accept(value.snapshot);
      this.patch({ lease: value.lease });
      await this.status();
    });
  submit = (choice: Command["choice"]) =>
    this.run(async () => {
      const v = this.state.view!;
      if (
        !v.decision ||
        !this.state.lease ||
        this.state.replay ||
        this.state.pending
      )
        return;
      this.patch({ pending: true });
      await this.call("command", {
        matchId: v.matchId,
        lease: this.state.lease,
        command: {
          protocolVersion: "2.0",
          commandId: this.id(),
          expectedMatchSeq: v.matchSeq,
          expectedStateVersion: v.stateVersion,
          controllerEpoch: v.controllerEpoch,
          decisionId: v.decision.id,
          choice,
        },
      });
      await this.status();
      this.accept(await this.call("snapshot", { matchId: v.matchId }));
    });
  recover = () =>
    this.run(async () => {
      const mid =
        this.state.view?.matchId || this.state.status?.pendingMatches[0];
      if (!mid) return;
      await this.call("recover", { matchId: mid });
      await this.status();
      if (this.state.view)
        this.accept(await this.call("snapshot", { matchId: mid }));
    });
  resolve = () =>
    this.run(async () => {
      const mid =
        this.state.view?.matchId || this.state.status?.pendingMatches[0];
      if (!mid) return;
      await this.call("resolve-rejected", { matchId: mid });
      await this.status();
    });
  resign = () =>
    this.run(async () => {
      const v = this.state.view!;
      if (!this.state.lease || this.state.pending || this.state.replay) return;
      this.patch({ pending: true });
      await this.call("resign", {
        matchId: v.matchId,
        lease: this.state.lease,
        command: {
          protocolVersion: "2.0",
          commandId: this.id(),
          expectedMatchSeq: v.matchSeq,
          expectedStateVersion: v.stateVersion,
          controllerEpoch: v.controllerEpoch,
        },
      });
      await this.status();
      this.accept(await this.call("snapshot", { matchId: v.matchId }));
    });
  leave = () =>
    this.run(async () => {
      if (this.state.view)
        await this.call("unwatch", { matchId: this.state.view.matchId });
      this.patch({
        showing: false,
        room: null,
        replay: null,
        playing: false,
        lease: "",
      });
    });
  replay = () =>
    this.run(async () => {
      const page = await this.call<ReplayPage>("replay", {
        matchId: this.state.view!.matchId,
      });
      this.patch({
        replay: page,
        frame: 0,
        nextCursor: page.nextCursor,
        playing: false,
      });
    });
  more = () =>
    this.run(async () => {
      const s = this.state;
      if (!s.nextCursor || !s.replay) return;
      if (s.replay.frames.length >= 300)
        throw new OnlineError("REPLAY_PAGE_LIMIT");
      const page = await this.call<ReplayPage>("replay", {
        matchId: s.view!.matchId,
        cursor: s.nextCursor,
      });
      if (
        page.viewerSeat !== s.replay.viewerSeat ||
        page.matchId !== s.replay.matchId
      )
        throw new OnlineError("IDENTITY_MISMATCH");
      this.patch({
        replay: {
          ...page,
          frames: [...s.replay.frames, ...page.frames] as ReplayFrame[],
        },
        nextCursor: page.nextCursor,
      });
    });
  seek = (frame: number) => {
    this.patch({ frame, playing: false });
    this.schedule();
  };
  play = (playing: boolean) => {
    this.patch({ playing });
    this.schedule();
  };
  speed = (speed: number) => {
    this.patch({ speed });
    this.schedule();
  };
  closeReplay = () => {
    this.patch({ replay: null, playing: false });
    this.schedule();
  };
  dispose() {
    this.disposed = true;
    clearTimeout(this.timer);
    void this.api.close().catch(() => {});
  }
}
