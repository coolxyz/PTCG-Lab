import type { CardInfo } from "@ptcg/battle-ui";
export class OnlineError extends Error {
  constructor(
    public code: string,
    public retryable = false,
    public status = 0,
  ) {
    super(code);
  }
}
export interface OnlineAPI {
  profiles(): Promise<{ id: string; origin: string }[]>;
  call<T>(profile: string, operation: string, body?: object): Promise<T>;
  catalog(
    catalogVersion: string,
    assetVersion: string,
  ): Promise<{ cards: CardInfo[]; missing?: boolean }>;
  close(): Promise<void>;
}
export async function authorizeProduct(code: string): Promise<void> {
  const r = await fetch("/authorize", {
    method: "POST",
    credentials: "same-origin",
    redirect: "error",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code.trim() }),
  });
  if (!r.ok)
    throw new OnlineError(
      (await r.json()).error?.code || "LOCAL_AUTH_REQUIRED",
    );
}
export async function connect(windowId: string): Promise<OnlineAPI> {
  async function request(
    path: string,
    body: object,
    capability?: string,
    keepalive = false,
  ) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const r = await fetch(path, {
        method: "POST",
        credentials: capability ? "omit" : "same-origin",
        redirect: "error",
        cache: "no-store",
        keepalive,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...(capability ? { "X-PTCG-Bridge": capability } : {}),
        },
        body: JSON.stringify(body),
      });
      const value = await r.json();
      if (!r.ok)
        throw new OnlineError(
          value.error?.code || "LOCAL_REQUEST_FAILED",
          !!value.error?.retryable,
          r.status,
        );
      return value;
    } catch (e) {
      if (e instanceof OnlineError) throw e;
      throw new OnlineError("CONNECTION_UNKNOWN", true);
    } finally {
      clearTimeout(timer);
    }
  }
  const { capability } = await request("/session", { windowId });
  const prefix = "/api/desktop/online/v1";
  return {
    profiles: async () =>
      (await request(prefix + "/profiles", {}, capability)).result,
    call: async <T>(profile: string, operation: string, body: object = {}) => {
      if (
        !/^[a-z0-9-]{1,32}$/.test(profile) ||
        !/^[a-z-]{1,32}$/.test(operation)
      )
        throw new OnlineError("INVALID_REQUEST");
      return (
        await request(
          `${prefix}/profiles/${profile}/${operation}`,
          body,
          capability,
        )
      ).result as T;
    },
    catalog: (catalogVersion, assetVersion) =>
      request("/catalog", { catalogVersion, assetVersion }, capability),
    close: async () => {
      await request(prefix + "/close-window", {}, capability, true);
    },
  };
}
