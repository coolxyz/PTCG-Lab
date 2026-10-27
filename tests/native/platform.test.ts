import assert from "node:assert/strict";
import test from "node:test";
import { platformServices } from "../../packages/platform-services/src/index.js";

test("desktop retains full product capabilities; mobile excludes local execution", () => {
  for (const platform of ["browser", "macos", "windows"] as const) {
    const value = platformServices(platform);
    assert.equal(value.capabilities.localPractice, true);
    assert.equal(value.capabilities.localRuleVerification, true);
    assert.equal(value.capabilities.onlineBattle, true);
    assert.equal(Object.isFrozen(value.capabilities), true);
  }
  const mobile = platformServices("ios");
  assert.equal(mobile.capabilities.localPractice, false);
  assert.equal(mobile.capabilities.localRuleVerification, false);
  assert.equal(mobile.capabilities.onlineBattle, true);
  assert.equal(mobile.capabilities.collection, true);
  assert.equal(mobile.capabilities.importExport, true);
});

test("unknown platforms cannot silently inherit desktop authority", () => {
  assert.throws(() => platformServices("unknown" as "macos"), /UNSUPPORTED_PLATFORM/);
});
