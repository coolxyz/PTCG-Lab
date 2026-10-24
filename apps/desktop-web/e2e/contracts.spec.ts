import { test, expect } from "@playwright/test";
import { demoSnapshot, demoReplay } from "../src/demo-data";
import { snapshotView, replayView } from "../src/presentation";

for (const seat of ["PLAYER1", "PLAYER2"] as const) {
  test(`${seat}: relative views, legal refs, rich effects and detached DTO`, () => {
    const wire = demoSnapshot(seat, "selection");
    wire.observation.self.active[0].card.presentation!.attackPowerReduction = 20;
    wire.observation.self.active[0].card.presentation!.attackProtection = {
      damage: true,
    };
    wire.visualEffects = [
      {
        kind: "hp",
        side: "self",
        zone: "active",
        index: 0,
        before: 100,
        after: 90,
      },
    ];
    const result = snapshotView(wire);
    expect(result.decision?.actor).toBe(seat);
    expect(result.decision?.candidates[0].ref).toBe("demo-choice-a");
    expect(result.observation.opponent.hand).toBeNull();
    expect(result.observation.self.active[0].attackPowerReduction).toBe(20);
    expect(result.observation.self.active[0].attackProtection).toEqual({
      damage: true,
    });
    expect(result.events?.[0].effects).toEqual(wire.visualEffects);
    result.observation.self.active[0].attackProtection!.damage = false;
    result.decision!.deckInspection!.cards[0].card.name = "changed";
    expect(
      wire.observation.self.active[0].card.presentation!.attackProtection!
        .damage,
    ).toBe(true);
    expect(
      wire.decision!.kind === "selection" &&
        wire.decision!.deckInspection!.cards[0].card.name,
    ).toBe("演示宝可梦甲");
  });
  test(`${seat}: non-turn decisions and readonly replay`, () => {
    const wire = demoSnapshot(seat, "non-turn");
    expect(wire.observation.turn).not.toBe(seat);
    expect(snapshotView(wire).decision?.actor).toBe(seat);
    wire.decision!.actor = seat === "PLAYER1" ? "PLAYER2" : "PLAYER1";
    expect(snapshotView(wire).decision).toBeNull();
    const page = demoReplay(seat);
    expect(replayView(page, 1).decision).toBeNull();
    expect(() => replayView(page, 9)).toThrow(RangeError);
  });
}
test("minimal v2 identities and board state work without presentation", () => {
  const wire = demoSnapshot();
  const active = wire.observation.self.active[0];
  delete active.card.presentation;
  Object.assign(active, {
    hp: 30,
    maximumHp: 100,
    energy: ["FIRE"],
    conditions: ["POISONED"],
    flags: ["abilitiesSuppressed"],
  });
  const result = snapshotView(wire).observation.self.active[0];
  expect(result).toMatchObject({
    name: "演示宝可梦甲",
    hp: 30,
    maximumHp: 100,
    poisoned: true,
    abilitiesSuppressed: true,
    ref: "demo-active",
  });
});
test("face-up prize bindings survive mapping", () => {
  const result = snapshotView(demoSnapshot("PLAYER2", "prize"));
  expect(result.decision?.candidates[0].card).toBeUndefined();
  expect(result.decision?.candidates[1]).toMatchObject({
    revealedPrizeIndex: 2,
    revealedPrizeSide: "self",
    card: { name: "演示宝可梦甲" },
  });
});

test("visual effects are retained even when a core snapshot has no log event", () => {
  const wire = demoSnapshot();
  wire.events = [];
  wire.visualEffects = [
    { kind: "hp", side: "self", zone: "active", before: 100, after: 90 },
  ];
  const result = snapshotView(wire);
  expect(result.events).toEqual([]);
  expect(result.visualEffects).toEqual(wire.visualEffects);
  result.visualEffects![0].after = 0;
  expect(wire.visualEffects[0].after).toBe(90);
});
