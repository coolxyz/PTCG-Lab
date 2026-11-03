import type { ChineseDetails } from "../../web/src/CardDescription";

export type Entry = { printingId: string; quantity: number };
export type Card = {
  printingId: string;
  cnName: string;
  englishName?: string;
  engineId: string;
  productCode?: string;
  collectorNumber?: string;
  category: string;
  hp?: number;
  hasArtwork: boolean;
  chineseDetails?: ChineseDetails;
};
export type Deck = {
  id: string;
  name: string;
  entries: Entry[];
  version: number;
};
export type Collection = {
  version: number;
  entries: Holding[];
};
export type Holding = {
  printingId: string;
  quantity: number;
  condition: string;
  notes: string;
  wishlist: boolean;
};
export const conditions = ["未标注", "全新", "良好", "使用痕迹"] as const;
export type Source = {
  source: "github-raw" | "cdn";
  baseUrl: string;
  capacityMiB: number;
};
export type MobileError = {
  code: string;
  message: string;
  retryAfterSeconds?: number;
};
export interface MobileOwner {
  request<T>(request: Record<string, unknown>): Promise<T>;
  online?<T>(request: Record<string, unknown>): Promise<T>;
  files?<T>(request: Record<string, unknown>): Promise<T>;
  artwork(printingId: string): Promise<{ bytes: number[]; mime: string }>;
}
declare global {
  interface Window {
    __TAURI__?: {
      core: {
        invoke<T>(command: string, args: Record<string, unknown>): Promise<T>;
      };
    };
  }
}

export function nativeOwner(): MobileOwner {
  const core = window.__TAURI__?.core;
  if (!core)
    throw {
      code: "NATIVE_REQUIRED",
      message: "请通过移动 App 打开；此页面需要本机数据服务。",
    };
  return {
    request: (request) => core.invoke("mobile_request", { request }),
    online: (request) => core.invoke("mobile_online", { request }),
    files: (request) => core.invoke("mobile_files", { request }),
    artwork: (printingId) => core.invoke("mobile_artwork", { printingId }),
  };
}
export function message(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    typeof error.message === "string"
  )
    return error.message;
  return "操作未完成，已保存的数据不会被清空，请重试。";
}
