import type { Command } from "@ptcg/battle-ui";
import type { Creation } from "./types";
export interface PracticeStorage {
  match(): string | null;
  saveMatch(id: string): void;
  forgetMatch(): void;
  pending(id: string): Command | null;
  savePending(id: string, command: Command | null): void;
  creation(): Creation | null;
  saveCreation(value: Creation | null): void;
  preferredRevision(): string | null;
}
/** Separate P3 namespace; no Cookie, bearer token, rules state or AI data is read. */
export function practiceStorage(
  local: Storage,
  session: Storage,
): PracticeStorage {
  const prefix = "ptcg-p3-practice:";
  const read = (key: string): unknown => {
    const value = local.getItem(prefix + key);
    if (!value) return null;
    try {
      return JSON.parse(value);
    } catch {
      return null;
    }
  };
  const write = (key: string, value: unknown) =>
    value === null
      ? local.removeItem(prefix + key)
      : local.setItem(prefix + key, JSON.stringify(value));
  return {
    match: () => local.getItem(prefix + "match"),
    saveMatch: (id) => local.setItem(prefix + "match", id),
    forgetMatch: () => local.removeItem(prefix + "match"),
    pending: (id) => {
      const p = read("pending:" + id) as Command | null;
      return p &&
        typeof p.commandId === "string" &&
        typeof p.decisionId === "string" &&
        Number.isInteger(p.expectedStateVersion) &&
        p.choice &&
        (typeof p.choice.optionId === "string" ||
          (Array.isArray(p.choice.selectedRefs) &&
            p.choice.selectedRefs.every((x) => typeof x === "string")))
        ? p
        : null;
    },
    savePending: (id, p) => write("pending:" + id, p),
    creation: () => {
      const p = read("creation") as Creation | null;
      return p &&
        typeof p.requestId === "string" &&
        typeof p.revisionId === "string" &&
        ["A1", "H2", "A2"].includes(p.aiLevel) &&
        (typeof p.opponent === "string" ||
          typeof p.opponentRevisionId === "string")
        ? p
        : null;
    },
    saveCreation: (p) => write("creation", p),
    preferredRevision: () => session.getItem("ptcg-preferred-revision"),
  };
}

export function browserPracticeStorage(): PracticeStorage {
  return practiceStorage(window.localStorage, window.sessionStorage);
}
