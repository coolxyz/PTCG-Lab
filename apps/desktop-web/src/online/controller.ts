import type {
  MatchSnapshot,
  ReplayPage,
  ReplayFrame,
} from "@ptcg/battle-protocol";
import type { CardInfo, Command } from "@ptcg/battle-ui";
import { OnlineError, type OnlineAPI } from "./transport";
import type { OnlineStorage, Remembered } from "./storage";
export type ProductDeck = {
  id: string;
  name: string;
  version: number;
  revisions: { id: string; number: number; count: number }[];
};
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
  recentMatches?: {
    matchId: string;
    viewerSeat: string;
    done: boolean;
    winner: string | null;
    matchSeq: number;
  }[];
  productPending?: {
    revisionId: string;
    ruleReleaseId: string;
    requestId: string;
  }[];
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
    LOCAL_AUTH_REQUIRED: "本机窗口授权已过期，请关闭并重新打开 APP 或本地启动器。",
    CREDENTIAL_BACKEND_UNAVAILABLE: "无法访问系统凭据存储，请允许访问后重新打开 APP。",
    CREDENTIAL_KEY_UNAVAILABLE: "系统凭据密钥不可用，请保留本地数据并检查 Keychain 访问权限。",
    NATIVE_IDENTITY_IMPORT_REQUIRED: "检测到旧版明文身份，请先关闭 APP 并按身份导入手顺迁移。",
    REAUTHENTICATION_REQUIRED:
      "身份需要重新认证。游客身份无法自动找回，请保留本地凭据和待确认记录。",
    LOCAL_CONTROL_REQUIRED: "另一个窗口已接管，请重新取得操作权。",
    CONTROL_LOST: "操作权已变化，请刷新后重新接管。",
    PROFILE_BUSY: "后台正在处理请求，请稍后重试。",
    CONNECTION_UNKNOWN:
      "连接中断，操作结果可能尚未确认。请恢复原请求，勿重复创建。",
    NETWORK_UNAVAILABLE: "远程连接中断，请检查连接并恢复原请求。",
    OUTCOME_UNKNOWN: "服务器尚未确认操作结果，请继续恢复，不能提交新操作。",
    INVALID_PAYLOAD:
      "服务器拒绝了所选卡组。请核对下方本地检查及服务器规则版本；当前 API 未返回逐卡拒绝明细，原卡组未被修改。",
    REPLAY_EMPTY: "这场对局暂时没有可读回放帧，请刷新后重试。",
    REPLAY_PAGE_LIMIT: "已达到本地回放加载上限。",
    REPLAY_CURSOR_STALLED: "回放分页未推进，请重新打开回放。",
    CAPACITY_EXCEEDED: "服务器请求较多，请稍后重试；已保存的对局和操作记录不会丢失。",
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
  history: (ReplayFrame | MatchSnapshot)[];
  replay: ReplayPage | null;
  frame: number;
  nextCursor: string | null;
  replayNotice: string;
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
    history: [],
    replay: null,
    frame: 0,
    nextCursor: null,
    replayNotice: "",
    playing: false,
    speed: 1,
    connected: false,
  };
  private displayVersion = "";
  private displayRequest = 0;
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private disposed = false;
  private polling?: Promise<void>;
  private logCursor?: string;
  private logThrough = -1;
  private initializingTries = 0;
  private nextReplayPageAt = 0;
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
    const version =
      this.state.replay?.frames[this.state.frame]?.catalogVersion ||
      this.state.view?.catalogVersion;
    if (version && version !== this.displayVersion)
      void this.loadDisplay(version, "unavailable");
  }
  private save(s: Partial<Remembered>) {
    const next = { ...this.state.remembered, ...s };
    this.storage.write(this.state.profile, next);
    this.patch({ remembered: next });
  }
  private async call<T>(op: string, body: object = {}): Promise<T> {
    // PROFILE_BUSY is a local rejection before dispatch, so retrying the same
    // body is safe. Other failures retain their existing idempotency handling.
    const profile = this.state.profile;
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.api.call<T>(profile, op, body);
      } catch (error) {
        if (
          !(error instanceof OnlineError) ||
          error.code !== "PROFILE_BUSY" ||
          attempt >= 5 ||
          this.disposed
        )
          throw error;
        await new Promise((resolve) =>
          setTimeout(resolve, 150 * (attempt + 1)),
        );
      }
    }
  }
  private async run(fn: () => Promise<void>) {
    if (this.state.busy || this.disposed) return;
    clearTimeout(this.timer);
    this.patch({ busy: true, error: "" });
    try {
      await this.polling;
      if (this.disposed) return;
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
    if (s.replay && s.playing && s.frame + 1 < s.replay.frames.length) {
      this.timer = setTimeout(() => {
        this.patch({ frame: this.state.frame + 1 });
        this.schedule();
      }, 1000 / s.speed);
      return;
    }
    if (s.replay && !s.nextCursor) {
      if (s.playing) this.patch({ playing: false });
      return;
    }
    if (
      s.replay ||
      (s.showing &&
        s.view &&
        (!s.view.done || this.logThrough < s.view.matchSeq)) ||
      (!s.showing &&
        s.room &&
        ["waiting", "initializing", "initialized"].includes(s.room.status))
    )
      this.timer = setTimeout(
        () => this.background(),
        s.replay
          ? Math.max(1500, this.nextReplayPageAt - Date.now())
          : s.showing ? 600 : 1500,
      );
  }
  private background() {
    if (this.disposed || this.state.busy || this.polling) return;
    this.polling = (async () => {
      try {
        if (this.state.replay) await this.fetchMore();
        else if (this.state.showing && this.state.view) {
          if (!this.state.view.done) {
            const f = await this.call<{
              event: { snapshot: MatchSnapshot } | null;
              done: boolean;
              error: { code: string } | null;
            }>("events", { matchId: this.state.view.matchId });
            if (this.disposed) return;
            if (f.event) this.accept(f.event.snapshot);
            if (f.done && !this.state.view.done)
              throw new OnlineError(f.error?.code || "STREAM_UNAVAILABLE");
          }
          await this.syncHistory();
        } else await this.room();
      } catch (e) {
        if (!(e instanceof OnlineError && e.code === "PROFILE_BUSY"))
          this.patch({ error: errorText(e), playing: false });
      } finally {
        this.polling = undefined;
        this.schedule();
      }
    })();
  }
  private mergeHistory(frames: (ReplayFrame | MatchSnapshot)[]) {
    const rows = new Map(this.state.history.map((f) => [f.matchSeq, f]));
    for (const f of frames) rows.set(f.matchSeq, f);
    this.patch({
      history: [...rows.values()]
        .sort((a, b) => a.matchSeq - b.matchSeq)
        .slice(-5000),
    });
  }
  private async syncHistory() {
    const view = this.state.view;
    if (!view || this.logThrough >= view.matchSeq) return;
    const page = await this.call<ReplayPage>("replay", {
      matchId: view.matchId,
      ...(this.logCursor ? { cursor: this.logCursor } : {}),
    });
    if (this.disposed || this.state.view?.matchId !== view.matchId) return;
    if (page.matchId !== view.matchId || page.viewerSeat !== view.viewerSeat)
      throw new OnlineError("IDENTITY_MISMATCH");
    this.mergeHistory(page.frames);
    this.logThrough = page.frames.at(-1)?.matchSeq ?? this.logThrough;
    if (page.nextCursor) this.logCursor = page.nextCursor;
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
    if (
      old &&
      old.matchId === view.matchId &&
      JSON.stringify(old) === JSON.stringify(view)
    )
      return;
    if (old?.matchId !== view.matchId) {
      this.logCursor = undefined;
      this.logThrough = -1;
      this.patch({ history: [] });
    }
    this.mergeHistory([view]);
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
    this.displayVersion = "";
    this.displayRequest++;
    this.logCursor = undefined;
    this.logThrough = -1;
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
      history: [],
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
    await this.loadDisplay(
      caps.resources.displayCatalogVersion,
      caps.resources.assetManifestVersion,
    );
    if (this.state.remembered.roomId) await this.room();
    else if (this.state.remembered.matchId)
      await this.openInner(this.state.remembered.matchId, true);
  }
  private async loadDisplay(catalogVersion: string, assetVersion: string) {
    const request = ++this.displayRequest;
    this.displayVersion = catalogVersion;
    this.patch({ cards: {}, missing: true });
    try {
      const catalog = await this.api.catalog(catalogVersion, assetVersion);
      if (this.disposed || request !== this.displayRequest) return;
      this.patch({
        cards: this.cardIndex(catalog.cards),
        missing: !!catalog.missing || !catalog.cards.length,
      });
    } catch {
      if (request === this.displayRequest)
        this.patch({ cards: {}, missing: true });
    }
  }
  private cardIndex(cards: CardInfo[]) {
    const result: Record<string, CardInfo> = Object.fromEntries(
      cards.filter((c) => !!c.engineId?.trim()).map((c) => [c.engineId, c]),
    );
    for (const c of [...cards].sort(
      (a, b) =>
        Number(a.engineId.startsWith("unverified:")) -
        Number(b.engineId.startsWith("unverified:")),
    ))
      for (const alias of [c.englishName, ...(c.aliases || [])])
        if (alias?.trim() && !Object.hasOwn(result, alias)) result[alias] = c;
    return result;
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
        (error.status === 400 ||
          error.status === 422 ||
          [
            "MATCH_VERSION_CONFLICT",
            "INVALID_PAYLOAD",
            "REVISION_NOT_FOUND",
            "REVISION_CORRUPT",
            "RULE_RELEASE_UNAVAILABLE",
          ].includes(error.code))
      )
        this.save({ job: undefined });
      throw error;
    }
    // Preserve the original request until both result metadata and clearance are durable.
    const next: Partial<Remembered> = ["deck", "product-deck"].includes(
      job.operation,
    )
      ? {
          revision: result.deckRevision,
          localRevisionId:
            job.operation === "product-deck"
              ? (job.body.revisionId as string)
              : undefined,
          rule: job.body.ruleReleaseId as string,
        }
      : job.operation === "leave-room"
        ? { roomId: undefined, matchId: undefined }
        : { roomId: result.roomId, matchId: result.matchId || undefined };
    this.save({ ...next, job: undefined });
    if (["deck", "product-deck"].includes(job.operation))
      this.patch({ rule: job.body.ruleReleaseId as string });
    if (job.operation === "product-deck") await this.status();
    if (!["deck", "product-deck"].includes(job.operation)) {
      this.patch({ room: job.operation === "leave-room" ? null : result });
      if (result.matchId) await this.enterRoomMatch(result);
    }
  }
  retryLobby = () => this.run(() => this.executeJob());
  recoverProduct = (body: {
    revisionId: string;
    ruleReleaseId: string;
    requestId: string;
  }) =>
    this.run(async () => {
      await this.job("product-deck", body);
      await this.status();
    });
  checkRevision = (revisionId: string) =>
    this.call<{
      total: number;
      issues: {
        code: string;
        explanation: string;
        suggestedFix: string;
        cardRefs: string[];
      }[];
    }>("product-check", { revisionId });
  productDecks = () => this.call<{ decks: ProductDeck[] }>("product-decks");
  freezeDeck = (deckId: string, expectedVersion: number) =>
    this.call("product-freeze", { deckId, expectedVersion });
  useRevision = (revisionId: string) =>
    this.run(async () => {
      if (this.state.remembered.job)
        throw new OnlineError("PENDING_LOBBY_REQUEST");
      this.save({ revision: undefined, localRevisionId: undefined });
      await this.job("product-deck", {
        revisionId,
        ruleReleaseId: this.state.rule,
        requestId: this.id(),
      });
    });
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
    if (room.status === "closed") {
      this.save({ roomId: undefined, matchId: undefined });
      this.patch({ room: null });
      await this.status();
      return;
    }
    try {
      await this.openInner(room.matchId, true);
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
  private async openInner(matchId: string, restoring = false) {
    const view = await this.call<MatchSnapshot>("snapshot", { matchId });
    // Restore an ongoing game, while completed games remain an explicit history choice.
    // Only clear navigation pointers; native credentials, history and pending requests remain.
    if (restoring && view.done) {
      this.save({ matchId: undefined, roomId: undefined });
      this.patch({ room: null });
      await this.status();
      return;
    }
    this.save({ matchId, roomId: undefined });
    this.accept(view);
    this.patch({ showing: true, replay: null, lease: "" });
    if (!view.done) await this.watch(matchId);
    await this.syncHistory();
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
      await this.status();
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
      if (!page.frames.length) throw new OnlineError("REPLAY_EMPTY");
      this.nextReplayPageAt = Date.now() + 1500;
      this.patch({
        replay: page,
        replayNotice: "",
        frame: 0,
        nextCursor: page.nextCursor,
        playing: false,
      });
    });
  more = () => this.run(() => this.fetchMore());
  private async fetchMore() {
    const s = this.state;
    if (!s.nextCursor || !s.replay) return;
    if (Date.now() < this.nextReplayPageAt) return;
    if (s.replay.frames.length >= 5000)
      throw new OnlineError("REPLAY_PAGE_LIMIT");
    this.nextReplayPageAt = Date.now() + 1500;
    let page: ReplayPage;
    try {
      page = await this.call<ReplayPage>("replay", {
        matchId: s.view!.matchId,
        cursor: s.nextCursor,
      });
    } catch (error) {
      if (this.disposed || this.state.replay !== s.replay) return;
      if (error instanceof OnlineError && error.status === 429 && error.retryable) {
        // Read-only pagination can retry the exact cursor after the server's
        // fixed minute budget resets. Never retry writes or discard frames.
        this.nextReplayPageAt = Date.now() + 65000;
        this.patch({ replayNotice: "服务器暂时限流，已保留回放进度，将在约一分钟后自动继续加载。" });
        return;
      }
      throw error;
    }
    if (this.disposed || this.state.replay !== s.replay) return;
    if (
      page.viewerSeat !== s.replay.viewerSeat ||
      page.matchId !== s.replay.matchId ||
      page.ruleReleaseId !== s.replay.ruleReleaseId
    )
      throw new OnlineError("IDENTITY_MISMATCH");
    if (page.nextCursor === s.nextCursor)
      throw new OnlineError("REPLAY_CURSOR_STALLED");
    const frames = new Map(s.replay.frames.map((f) => [f.matchSeq, f]));
    for (const frame of page.frames) frames.set(frame.matchSeq, frame);
    this.patch({
      replay: {
        ...page,
        frames: [...frames.values()].sort((a, b) => a.matchSeq - b.matchSeq),
      },
      nextCursor: page.nextCursor,
      replayNotice: "",
    });
  }
  openReplay = (matchId: string) =>
    this.run(async () => {
      await this.openInner(matchId);
      const page = await this.call<ReplayPage>("replay", { matchId });
      if (!page.frames.length) throw new OnlineError("REPLAY_EMPTY");
      this.nextReplayPageAt = Date.now() + 1500;
      this.patch({
        replay: page,
        replayNotice: "",
        frame: 0,
        nextCursor: page.nextCursor,
        playing: false,
      });
    });
  seek = (frame: number) => {
    const end = (this.state.replay?.frames.length || 1) - 1;
    this.patch({ frame: Math.max(0, Math.min(end, frame)), playing: false });
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
    this.nextReplayPageAt = 0;
    this.patch({ replay: null, playing: false, replayNotice: "" });
    this.schedule();
  };
  dispose() {
    this.disposed = true;
    clearTimeout(this.timer);
    void this.api.close().catch(() => {});
  }
}
