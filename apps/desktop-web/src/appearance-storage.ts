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
