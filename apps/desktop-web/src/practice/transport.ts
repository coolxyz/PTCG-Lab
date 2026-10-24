import type { PracticeAPI } from "./types";
export class PracticeAPIError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
/** Fixed same-origin legacy practice routes. No configurable remote proxy or token access. */
export function practiceAPI(): PracticeAPI {
  async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch("/api/battle" + path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok)
      throw new PracticeAPIError(
        data.message || data.detail?.[0]?.msg || "请求失败，请刷新重试",
        response.status,
      );
    return data;
  }
  const match = (id: string) => "/matches/" + encodeURIComponent(id);
  return {
    session: () => request("/session", {}),
    revisions: () => request("/revisions"),
    matches: () => request("/matches"),
    preset: (id) =>
      request("/presets/" + encodeURIComponent(id) + "/revision", {}),
    create: (body) => request("/matches", body),
    view: (id) => request(match(id)),
    command: (id, body) => request(match(id) + "/commands", body),
    advance: (id) => request(match(id) + "/advance?steps=1", {}),
    resign: (id, version) =>
      request(match(id) + "/resign", { expectedStateVersion: version }),
    timeline: (id) => request(match(id) + "/replay/timeline"),
    replay: (id, seq) =>
      request(match(id) + `/replay?after=${seq - 1}&limit=1`),
  };
}
