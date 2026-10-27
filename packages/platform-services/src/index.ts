/** Product capability contract. Native secrets never cross this interface. */
export type Platform = "browser" | "macos" | "windows" | "ios";
export type PlatformServices = Readonly<{
  schema: 1;
  platform: Platform;
  capabilities: Readonly<{
    localPractice: boolean;
    localRuleVerification: boolean;
    onlineBattle: boolean;
    collection: boolean;
    importExport: boolean;
  }>;
}>;

export function platformServices(platform: Platform): PlatformServices {
  if (!["browser", "macos", "windows", "ios"].includes(platform)) {
    throw new Error("UNSUPPORTED_PLATFORM");
  }
  return Object.freeze({
    schema: 1,
    platform,
    capabilities: Object.freeze({
      localPractice: platform !== "ios",
      localRuleVerification: platform !== "ios",
      onlineBattle: true,
      collection: true,
      importExport: true,
    }),
  });
}

/** Only a descriptor; it neither promises an implemented adapter nor grants access. */
export interface NativeLifecycle {
  readonly state: "starting" | "ready" | "stopping" | "failed";
  readonly failureCode?: "BACKEND_START_FAILED" | "BACKEND_EXITED" | "INVALID_BUNDLE";
}

export interface ProductAdapter {
  readonly services: PlatformServices;
  /** Same-origin product operations; the native owner handles remote identity/TLS. */
  request(path: string, init?: RequestInit): Promise<Response>;
}
