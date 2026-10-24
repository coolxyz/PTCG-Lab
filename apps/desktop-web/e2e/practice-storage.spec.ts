import { test, expect } from "@playwright/test";
import { practiceStorage } from "../src/practice/storage";

function memory(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    key: (i) => [...data.keys()][i] ?? null,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => {
      data.set(k, v);
    },
    removeItem: (k) => {
      data.delete(k);
    },
    clear: () => data.clear(),
  };
}

test("legacy resume pointer can be adopted and rollback can select a newer match", () => {
  const local = memory(),
    session = memory(),
    store = practiceStorage(local, session);
  local.setItem("ptcg-match", "old-match");
  expect(store.match()).toBe("old-match");
  store.saveMatch("old-match");
  expect(local.getItem("ptcg-p3-practice:match")).toBe("old-match");
  store.saveMatch("new-match");
  expect(local.getItem("ptcg-match")).toBe("new-match");
  local.setItem("ptcg-match", "rollback-created-match");
  expect(store.match()).toBe("rollback-created-match");
  store.forgetMatch();
  expect(store.match()).toBeNull();
});

test("pointer compatibility does not rewrite pending receipts or preferred deck", () => {
  const local = memory(),
    session = memory(),
    store = practiceStorage(local, session);
  const command = {
    commandId: "same-receipt",
    decisionId: "same-decision",
    expectedStateVersion: 4,
    choice: { optionId: "same-option" },
  };
  store.savePending("old-match", command);
  session.setItem("ptcg-preferred-revision", "revision");
  store.saveMatch("another-match");
  store.forgetMatch();
  expect(store.pending("old-match")).toEqual(command);
  expect(store.takePreferredRevision()).toBe("revision");
  expect(store.takePreferredRevision()).toBeNull();
  session.setItem("ptcg-preferred-revision", "revision");
  expect(store.takePreferredRevision()).toBe("revision");
  local.setItem("ptcg-p3-practice:pending:broken", "invalid-json");
  expect(store.pending("broken")).toBeNull();
});
