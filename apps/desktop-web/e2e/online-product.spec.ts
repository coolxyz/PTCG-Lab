import { test, expect } from "@playwright/test";
import { OnlineController } from "../src/online/controller";
import { OnlineError, type OnlineAPI } from "../src/online/transport";
import type { Remembered, OnlineStorage } from "../src/online/storage";

test("recovered product submission keeps its rule and rejection cannot reuse an earlier deck", async () => {
  let saved: Remembered = { userId: "user" };
  let roomBody: any;
  const storage: OnlineStorage = {
    windowId: () => "window",
    selected: () => "lan",
    read: () => saved,
    write: (_profile, value) => {
      saved = value;
    },
  };
  const api: OnlineAPI = {
    profiles: async () => [{ id: "lan", origin: "https://example.test" }],
    catalog: async () => ({ cards: [], missing: true }),
    close: async () => {},
    call: async <T>(_profile: string, operation: string, body: any = {}) => {
      const results: Record<string, unknown> = {
        status: {
          identity: { userId: "user" },
          reauthenticationRequired: false,
          pendingMatches: [],
          recovery: { commands: [], controls: [] },
        },
        capabilities: {
          ruleReleaseIds: ["new-rule", "old-rule"],
          acceptingMatches: true,
          resources: {
            displayCatalogVersion: "catalog",
            assetManifestVersion: "unavailable",
          },
        },
      };
      if (operation === "product-deck") {
        if (body.revisionId === "rejected")
          throw new OnlineError("INVALID_PAYLOAD", false, 422);
        return { deckRevision: "remote-version" } as T;
      }
      if (operation === "create-room") {
        roomBody = body;
        return {
          roomId: "room",
          ruleReleaseId: body.ruleReleaseId,
          status: "closed",
          members: [],
          matchId: null,
        } as T;
      }
      return results[operation] as T;
    },
  };
  const controller = new OnlineController(api, storage, () => "00000000-0000-4000-8000-000000000001");
  await controller.start();
  expect(controller.snapshot().rule).toBe("new-rule");
  await controller.recoverProduct({
    revisionId: "immutable-version",
    ruleReleaseId: "old-rule",
    requestId: "original",
  });
  expect(controller.snapshot().rule).toBe("old-rule");
  await controller.create();
  expect(roomBody.ruleReleaseId).toBe("old-rule");
  expect(roomBody.deckRevision).toBe("remote-version");
  await controller.useRevision("rejected");
  expect(controller.snapshot().remembered.revision).toBeUndefined();
  expect(controller.snapshot().remembered.job).toBeUndefined();
  await controller.dispose();
});
