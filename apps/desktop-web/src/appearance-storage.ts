import {
  DEFAULT_APPEARANCE,
  parseAppearance,
  type AppearanceStorage,
} from "@ptcg/battle-ui";

const KEY = "ptcg.battle.appearance.v1";
/** Only visual preferences. Never credentials, game state, or remote assets. */
export const browserAppearanceStorage: AppearanceStorage = {
  load() {
    try {
      return parseAppearance(localStorage.getItem(KEY));
    } catch {
      return DEFAULT_APPEARANCE;
    }
  },
  save(value) {
    // Let the display report quota/privacy failures while keeping this session usable.
    localStorage.setItem(KEY, JSON.stringify(value));
  },
};

const PROFILE_APPEARANCE = "/api/desktop/appearance/v1";
let pendingSave = Promise.resolve();

export const productAppearanceStorage: AppearanceStorage = {
  load: browserAppearanceStorage.load,
  async restore() {
    const lastSave = pendingSave;
    await lastSave;
    const response = await fetch(PROFILE_APPEARANCE);
    if (response.status === 404) return browserAppearanceStorage.load();
    if (!response.ok) throw new Error("Appearance read failed");
    const raw = await response.text();
    if (raw !== "null") return parseAppearance(raw);

    let legacy: string | null = null;
    try {
      legacy = localStorage.getItem(KEY);
    } catch {
      // Browser storage may be disabled even when the native profile is writable.
    }
    const appearance = parseAppearance(legacy);
    if (legacy !== null && pendingSave === lastSave)
      await productAppearanceStorage.save(appearance);
    return appearance;
  },
  save(value) {
    const save = pendingSave.then(async () => {
      const response = await fetch(PROFILE_APPEARANCE, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(value),
      });
      if (response.status === 404) browserAppearanceStorage.save(value);
      else if (!response.ok) throw new Error("Appearance save failed");
    });
    pendingSave = save.catch(() => {});
    return save;
  },
};
