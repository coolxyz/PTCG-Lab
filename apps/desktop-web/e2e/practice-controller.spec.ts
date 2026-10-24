import { test, expect } from "@playwright/test";
import { PracticeController } from "../src/practice/controller";
import { PracticeAPIError } from "../src/practice/transport";
import type {
  PracticeAPI,
  PracticeView,
  Creation,
} from "../src/practice/types";
import type { PracticeStorage } from "../src/practice/storage";
import type { Command } from "@ptcg/battle-ui";
import { snapshotView } from "../src/presentation";
import { demoSnapshot } from "../src/demo-data";
const controllers: PracticeController[] = [];
test.afterEach(() => {
  for (const c of controllers.splice(0)) c.setActive(false);
});
function fixture() {
  let saved: string | null = null,
    creation: Creation | null = null;
  const pending = new Map<string, Command>();
  const storage: PracticeStorage = {
    match: () => saved,
    saveMatch: (id) => {
      saved = id;
    },
    forgetMatch: () => {
      saved = null;
    },
    pending: (id) => pending.get(id) ?? null,
    savePending: (id, c) => {
      if (c) pending.set(id, structuredClone(c));
      else pending.delete(id);
    },
    creation: () => creation,
    saveCreation: (c) => {
      creation = c ? structuredClone(c) : null;
    },
    takePreferredRevision: () => null,
  };
  const view = (): PracticeView => snapshotView(demoSnapshot());
  const calls: {
    creates: Creation[];
    commands: Command[];
    sessions: number;
    advances: number;
  } = { creates: [], commands: [], sessions: 0, advances: 0 };
  const api: PracticeAPI = {
    session: async () => {
      calls.sessions++;
      return {
        mode: "local-practice",
        cards: [],
        engineVersion: "demo",
        presets: [],
        opponents: [{ id: "demo-ai", name: "演示 AI" }],
      };
    },
    revisions: async () => [
      { id: "revision", name: "演示", number: 1, playable: true },
    ],
    matches: async () => [],
    preset: async () => ({
      id: "revision",
      name: "演示",
      number: 1,
      playable: true,
    }),
    create: async (body) => {
      calls.creates.push(structuredClone(body));
      return view();
    },
    view: async () => view(),
    command: async (_id, body) => {
      calls.commands.push(structuredClone(body));
      return { view: { ...view(), stateVersion: 2 } };
    },
    advance: async () => {
      calls.advances++;
      return view();
    },
    resign: async () => ({
      ...view(),
      done: true,
      status: "resigned",
      winner: "PLAYER2",
      decision: null,
    }),
    timeline: async () => ({ lastSeq: 2, checkpoints: [] }),
    replay: async (_id, seq) => ({ frames: [{ seq, view: view() }] }),
  };
  let counter = 0;
  const make = () => {
    const c = new PracticeController(
      api,
      storage,
      () => `synthetic-id-${++counter}`,
    );
    controllers.push(c);
    return c;
  };
  return { api, storage, calls, make, view };
}
async function ready(c: PracticeController) {
  c.setActive(true);
  await expect.poll(() => c.snapshot().initialized).toBe(true);
}
test("StrictMode reactivation initializes only once and H2 remains default", async () => {
  const f = fixture(),
    c = f.make();
  c.setActive(true);
  c.setActive(false);
  c.setActive(true);
  await expect.poll(() => c.snapshot().initialized).toBe(true);
  expect(f.calls.sessions).toBe(1);
  expect(c.snapshot().aiLevel).toBe("H2");
  await c.create();
  expect(f.calls.creates[0]).toMatchObject({
    aiLevel: "H2",
    opponent: "demo-ai",
  });
  c.leave();
  await expect.poll(() => c.snapshot().busy).toBe(false);
  c.resume();
  c.setActive(false);
  c.setActive(true);
  expect(f.calls.creates).toHaveLength(1);
  expect(c.snapshot().view?.matchId).toBe("demo-match");
});
test("unknown creation retry survives reload, freezes selections and uses same request", async () => {
  const f = fixture();
  const create = f.api.create;
  let fail = true;
  f.api.create = async (b) => {
    const result = await create(b);
    if (fail) throw new Error("lost reply");
    return result;
  };
  const c = f.make();
  await ready(c);
  await c.create();
  c.select("aiLevel", "A1");
  expect(c.snapshot().aiLevel).toBe("H2");
  c.setActive(false);
  const next = f.make();
  await ready(next);
  fail = false;
  await next.create();
  expect(f.calls.creates).toHaveLength(2);
  expect(f.calls.creates[1]).toEqual(f.calls.creates[0]);
  expect(f.storage.creation()).toBeNull();
});
test("pending commands survive refresh and reload; retry uses exact receipt identity", async () => {
  const f = fixture();
  const send = f.api.command;
  let fail = true;
  f.api.command = async (id, c) => {
    const result = await send(id, c);
    if (fail) throw new Error("lost reply");
    return result;
  };
  const c = f.make();
  await ready(c);
  await c.create();
  await c.submit({ optionId: "demo-pass" });
  await c.refresh();
  expect(c.snapshot().pending).not.toBeNull();
  await c.submit({ optionId: "another" });
  expect(f.calls.commands).toHaveLength(1);
  c.leave();
  await expect.poll(() => c.snapshot().busy).toBe(false);
  c.resume();
  expect(c.snapshot().error).toContain("待确认");
  c.setActive(false);
  const next = f.make();
  await ready(next);
  fail = false;
  await next.retry();
  expect(f.calls.commands).toHaveLength(2);
  expect(f.calls.commands[1]).toEqual(f.calls.commands[0]);
  expect(f.storage.pending("demo-match")).toBeNull();
});
test("double click is serialized before creating a second command", async () => {
  const f = fixture(),
    c = f.make();
  await ready(c);
  await c.create();
  let release!: () => void;
  const send = f.api.command;
  f.api.command = async (id, body) => {
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    return send(id, body);
  };
  const first = c.submit({ optionId: "demo-pass" });
  await c.submit({ optionId: "demo-pass" });
  release();
  await first;
  expect(f.calls.commands).toHaveLength(1);
});
test("only definitive rejection clears pending; transient resume error preserves saved match", async () => {
  const f = fixture(),
    c = f.make();
  await ready(c);
  await c.create();
  f.api.command = async () => {
    throw new PracticeAPIError("stale", 409);
  };
  await c.submit({ optionId: "demo-pass" });
  expect(c.snapshot().pending).toBeNull();
  c.setActive(false);
  f.api.view = async () => {
    throw new Error("offline");
  };
  const next = f.make();
  next.setActive(true);
  await expect.poll(() => next.snapshot().error).toBe("offline");
  expect(f.storage.match()).toBe("demo-match");
});
test("private replay stays read-only, no command or AI action on hidden page/coin playback", async () => {
  const f = fixture(),
    c = f.make();
  await ready(c);
  await c.create();
  await c.seek(0);
  await c.submit({ optionId: "demo-pass" });
  await c.advance();
  expect(f.calls.commands).toHaveLength(0);
  expect(f.calls.advances).toBe(0);
  c.closeReplay();
  f.api.view = async () => ({ ...f.view(), decision: null });
  await c.refresh();
  c.setCoinPlaying(true);
  await c.advance();
  expect(f.calls.advances).toBe(0);
  c.setActive(false);
  c.setCoinPlaying(false);
  await c.advance();
  expect(f.calls.advances).toBe(0);
});
test("leaving during an accepted AI request retains its result without starting another loop", async () => {
  const f = fixture(),
    c = f.make();
  await ready(c);
  await c.create();
  f.api.view = async () => ({ ...f.view(), decision: null });
  await c.refresh();
  let finish!: (value: PracticeView) => void;
  f.api.advance = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const pending = c.advance();
  c.setActive(false);
  finish({ ...f.view(), stateVersion: 8 });
  await pending;
  expect(c.snapshot().view?.stateVersion).toBe(8);
  expect(c.snapshot().active).toBe(false);
  c.setActive(true);
  expect(c.snapshot().view?.stateVersion).toBe(8);
  expect(f.calls.creates).toHaveLength(1);
});

test("returning from construction refreshes revisions and honors a newly chosen version", async () => {
  const f = fixture(),
    c = f.make();
  await ready(c);
  c.setActive(false);
  f.storage.takePreferredRevision = () => "new-revision";
  f.api.revisions = async () => [
    { id: "new-revision", name: "new", number: 2, playable: true },
  ];
  c.setActive(true);
  await expect.poll(() => c.snapshot().revision).toBe("new-revision");
  expect(f.calls.sessions).toBe(1);
  expect(f.calls.creates).toHaveLength(0);
});

test("navigation keeps uncertain action errors and pending receipts visible", async () => {
  const f = fixture(),
    c = f.make();
  await ready(c);
  await c.create();
  f.api.command = async () => {
    throw new Error("lost reply");
  };
  await c.submit({ optionId: "demo-pass" });
  c.setActive(false);
  c.setActive(true);
  await expect.poll(() => c.snapshot().busy).toBe(false);
  expect(c.snapshot().error).toBe("lost reply");
  expect(c.snapshot().pending).not.toBeNull();
});
