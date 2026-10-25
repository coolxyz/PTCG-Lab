export interface Remembered {
  userId: string;
  roomId?: string;
  matchId?: string;
  revision?: string;
  job?: { operation: string; body: Record<string, unknown> };
}
export interface OnlineStorage {
  windowId(): string;
  selected(value?: string): string;
  read(profile: string, user: string): Remembered;
  write(profile: string, value: Remembered): void;
}
/** Only non-secret per-window navigation and idempotency data; never credentials. */
export const onlineStorage: OnlineStorage = {
  windowId() {
    let id = sessionStorage.getItem("ptcg-online-window");
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem("ptcg-online-window", id);
    }
    return id;
  },
  selected(value) {
    if (value !== undefined)
      sessionStorage.setItem("ptcg-online-profile", value);
    return sessionStorage.getItem("ptcg-online-profile") || "";
  },
  read(profile, userId) {
    try {
      const saved = JSON.parse(
        sessionStorage.getItem("ptcg-online-" + profile) || "null",
      );
      return saved?.userId === userId ? saved : { userId };
    } catch {
      return { userId };
    }
  },
  write(profile, value) {
    sessionStorage.setItem("ptcg-online-" + profile, JSON.stringify(value));
  },
};
