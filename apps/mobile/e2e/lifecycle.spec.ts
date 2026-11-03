import { test, expect } from "@playwright/test";
import { OnlineController } from "../../desktop-web/src/online/controller";
import type { OnlineAPI } from "../../desktop-web/src/online/transport";
import type {
  OnlineStorage,
  Remembered,
} from "../../desktop-web/src/online/storage";
import { demoSnapshot, demoReplay } from "../../desktop-web/src/demo-data";

test("resume refreshes the same identity, drops a late lease and never replays a write", async () => {
  const view = demoSnapshot("PLAYER1", "options");
  let saved: Remembered = { userId: "same-user" };
  let controlStarted!: () => void, releaseControl!: () => void;
  const started = new Promise<void>((r) => {
    controlStarted = r;
  });
  const held = new Promise<void>((r) => {
    releaseControl = r;
  });
  const operations: string[] = [],
    lifecycle: boolean[] = [];
  const storage: OnlineStorage = {
    windowId: () => "same-window",
    selected: () => "same-server",
    read: () => saved,
    write: (_, v) => {
      saved = v;
    },
  };
  const api: OnlineAPI = {
    profiles: async () => [
      { id: "same-server", origin: "https://battle.example.test" },
    ],
    catalog: async () => ({ cards: [], missing: true }),
    close: async () => {},
    lifecycle: async (active) => {
      lifecycle.push(active);
    },
    call: async <T>(_: string, op: string) => {
      operations.push(op);
      if (op === "status")
        return {
          identity: { userId: "same-user" },
          pendingMatches: [],
          recovery: { commands: [], controls: [] },
        } as T;
      if (op === "capabilities")
        return {
          ruleReleaseIds: [view.ruleReleaseId],
          resources: {
            displayCatalogVersion: view.catalogVersion,
            assetManifestVersion: "unavailable",
          },
        } as T;
      if (op === "snapshot") return structuredClone(view) as T;
      if (op === "replay")
        return { ...demoReplay("PLAYER1"), matchId: view.matchId } as T;
      if (op === "control") {
        controlStarted();
        await held;
        return {
          lease: "retired-local-lease",
          snapshot: structuredClone(view),
        } as T;
      }
      return {} as T;
    },
  };
  const c = new OnlineController(api, storage);
  try {
    await c.start();
    await c.open(view.matchId);
    const pendingControl = c.control();
    await started;
    c.suspend();
    await c.submit({ optionId: "never-send" });
    const resuming = c.resume();
    releaseControl();
    await pendingControl;
    await resuming;
    expect(lifecycle).toEqual([false, true]);
    expect(c.snapshot().lease).toBe("");
    expect(c.snapshot().status?.identity?.userId).toBe("same-user");
    expect(
      operations.filter((op) => ["guest", "command", "resign"].includes(op)),
    ).toEqual([]);
    expect(operations.filter((op) => op === "control")).toHaveLength(1);
    expect(c.snapshot().error).toBe("");
  } finally {
    releaseControl();
    c.dispose();
  }
});
