import {
  OnlineError,
  type OnlineAPI,
} from "../../desktop-web/src/online/transport";
import type {
  OnlineStorage,
  Remembered,
} from "../../desktop-web/src/online/storage";
import type { MobileOwner } from "./contracts";

export async function nativeOnline<T>(
  owner: MobileOwner,
  request: Record<string, unknown>,
): Promise<T> {
  if (!owner.online) throw new OnlineError("NATIVE_REQUIRED");
  try {
    return await owner.online<T>(request);
  } catch (error) {
    const e = error as { code?: string; retryable?: boolean; status?: number };
    throw new OnlineError(
      e?.code || "LOCAL_REQUEST_FAILED",
      !!e?.retryable,
      e?.status || 0,
    );
  }
}
export function onlineAPI(owner: MobileOwner): OnlineAPI {
  return {
    profiles: () => nativeOnline(owner, { op: "profiles" }),
    call: (profile, operation, body = {}) =>
      nativeOnline(owner, { op: "call", profile, operation, body }),
    catalog: (catalogVersion, assetVersion) =>
      nativeOnline(owner, { op: "catalog", catalogVersion, assetVersion }),
    close: () => nativeOnline(owner, { op: "close" }),
    lifecycle: (active) => nativeOnline(owner, { op: "lifecycle", active }),
  };
}
// Navigation and retry IDs only. Tokens, controllers and private frames remain native.
const prefix = "ptcg-mobile-online:";
export const mobileOnlineStorage: OnlineStorage = {
  windowId() {
    let id = sessionStorage.getItem(prefix + "window");
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem(prefix + "window", id);
    }
    return id;
  },
  selected(value) {
    if (value !== undefined) localStorage.setItem(prefix + "profile", value);
    return localStorage.getItem(prefix + "profile") || "";
  },
  read(profile, userId) {
    const raw = localStorage.getItem(prefix + profile);
    if (!raw) return { userId };
    try {
      const value = JSON.parse(raw) as Remembered;
      return value?.userId === userId ? value : { userId };
    } catch {
      throw new OnlineError("LOCAL_STATE_FAILED");
    }
  },
  write(profile, value) {
    localStorage.setItem(prefix + profile, JSON.stringify(value));
  },
};
