import assert from "node:assert/strict";
import { OnlineController } from "../../apps/desktop-web/src/online/controller";
import {
  OnlineError,
  type OnlineAPI,
} from "../../apps/desktop-web/src/online/transport";
import type {
  OnlineStorage,
  Remembered,
} from "../../apps/desktop-web/src/online/storage";
import { demoSnapshot, demoReplay } from "../../apps/desktop-web/src/demo-data";
class Store implements OnlineStorage {
  rows = new Map<string, Remembered>();
  selectedId = "";
  windowId() {
    return "w";
  }
  selected(value?: string) {
    if (value) this.selectedId = value;
    return this.selectedId;
  }
  read(p: string, userId: string) {
    const r = this.rows.get(p);
    return structuredClone(r?.userId === userId ? r : { userId });
  }
  write(p: string, v: Remembered) {
    this.rows.set(p, structuredClone(v));
  }
}
class API implements OnlineAPI {
  view = demoSnapshot("PLAYER2", "options");
  pending: string[] = [];
  calls: { p: string; op: string; body: any }[] = [];
  fail = "";
  async profiles() {
    return [
      { id: "one", origin: "https://one.example" },
      { id: "two", origin: "https://two.example" },
    ];
  }
  async catalog() {
    return { cards: [], missing: true };
  }
  async close() {}
  async call<T>(p: string, op: string, body: any = {}): Promise<T> {
    this.calls.push({ p, op, body: structuredClone(body) });
    let value: any;
    switch (op) {
      case "status":
        value = {
          identity: { userId: p },
          pendingMatches: this.pending,
          reauthenticationRequired: false,
          recovery: { commands: [], controls: [] },
        };
        break;
      case "capabilities":
        value = {
          ruleReleaseIds: ["rule"],
          acceptingMatches: true,
          resources: { displayCatalogVersion: "c", assetManifestVersion: "a" },
        };
        break;
      case "snapshot":
        value = this.view;
        break;
      case "control":
        value = { lease: "lease", snapshot: this.view };
        break;
      case "watch":
      case "unwatch":
        value = {};
        break;
      case "deck":
        if (this.fail === "unknown")
          throw new OnlineError("CONNECTION_UNKNOWN", true);
        if (this.fail === "reject")
          throw new OnlineError("INVALID_PAYLOAD", false, 400);
        value = { deckRevision: "revision" };
        break;
      case "command":
        this.pending = [this.view.matchId];
        if (this.fail === "unknown")
          throw new OnlineError("CONNECTION_UNKNOWN", true);
        value = {};
        this.pending = [];
        break;
      case "recover":
        this.pending = [];
        value = { receipt: { commandId: "original" } };
        break;
      case "replay":
        value = demoReplay("PLAYER2");
        break;
      default:
        throw Error("Unexpected operation " + op);
    }
    return structuredClone(value);
  }
}
{
  const a = new API(),
    store = new Store(),
    c = new OnlineController(a, store, () => "fixed-id");
  await c.start();
  a.fail = "unknown";
  await c.deck('[{"printingId":"x","quantity":1}]');
  assert(c.snapshot().remembered.job);
  c.dispose();
  const d = new OnlineController(a, store);
  await d.start();
  a.fail = "";
  await d.retryLobby();
  const calls = a.calls.filter((x) => x.op === "deck");
  assert.deepEqual(calls[0].body, calls[1].body);
  assert.equal(d.snapshot().remembered.job, undefined);
  d.dispose();
}
{
  const a = new API(),
    c = new OnlineController(a, new Store());
  await c.start();
  a.fail = "reject";
  await c.deck("[]");
  assert.equal(c.snapshot().remembered.job, undefined);
  c.dispose();
}
{
  const a = new API(),
    c = new OnlineController(a, new Store());
  await c.start();
  await c.open(a.view.matchId);
  await c.control();
  a.fail = "unknown";
  await c.submit({ optionId: "test" });
  assert(c.snapshot().pending);
  await c.submit({ optionId: "second" });
  assert.equal(a.calls.filter((x) => x.op === "command").length, 1);
  await c.recover();
  assert(!c.snapshot().pending);
  await c.replay();
  const n = a.calls.filter((x) => x.op === "command").length;
  await c.submit({ optionId: "replay-forbidden" });
  assert.equal(a.calls.filter((x) => x.op === "command").length, n);
  await c.leave();
  assert(!a.calls.some((x) => x.op === "resign"));
  await c.select("two");
  assert.equal(c.snapshot().lease, "");
  assert.equal(c.snapshot().view, null);
  assert.equal(c.snapshot().remembered.userId, "two");
  assert(!a.calls.some((x) => /ai|advance/.test(x.op)));
  c.dispose();
}
console.log("3 online controller recovery/isolation scenarios passed");
{
  const api = new API(),
    store = new Store();
  store.write("one", {
    userId: "one",
    roomId: "room",
    matchId: api.view.matchId,
  });
  const original = api.call.bind(api);
  let unavailable = true;
  api.call = async <T>(
    p: string,
    op: string,
    body: object = {},
  ): Promise<T> => {
    if (op === "room")
      return {
        roomId: "room",
        matchId: api.view.matchId,
        status: "initialized",
      } as T;
    if (op === "snapshot" && unavailable) {
      unavailable = false;
      throw new OnlineError("FORBIDDEN", false, 403);
    }
    return original(p, op, body);
  };
  const c = new OnlineController(api, store);
  await c.start();
  assert.equal(c.snapshot().error, "");
  assert.equal(c.snapshot().showing, false);
  await c.refresh();
  assert(c.snapshot().showing);
  c.dispose();
}
console.log("Initialization promotion gap scenario passed");

{
  const api = new API(),
    store = new Store();
  const original = api.call.bind(api);
  let rules = ["rule", "second-rule"];
  api.call = async <T>(p: string, op: string, body = {}): Promise<T> => {
    const result = await original<any>(p, op, body);
    if (op === "capabilities") result.ruleReleaseIds = rules;
    return result;
  };
  const first = new OnlineController(api, store);
  await first.start();
  first.rule("second-rule");
  await first.deck("[]");
  first.dispose();
  const restored = new OnlineController(api, store);
  await restored.start();
  assert.equal(restored.snapshot().rule, "second-rule");
  assert.equal(restored.snapshot().remembered.revision, "revision");
  restored.dispose();
  rules = ["rule"];
  const changed = new OnlineController(api, store);
  await changed.start();
  assert.equal(changed.snapshot().rule, "rule");
  assert.equal(changed.snapshot().remembered.revision, undefined);
  changed.dispose();
}
console.log(
  "Rule-bound deck revision survives reload and clears on unavailable rule",
);
