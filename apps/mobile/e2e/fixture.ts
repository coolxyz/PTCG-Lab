import type { Page } from "@playwright/test";
export async function fixture(page: Page) {
  await page.addInitScript(() => {
    const cards = [
      {
        printingId: "CN:TEST:001",
        cnName: "测试宝可梦",
        englishName: "Test",
        engineId: "E1",
        category: "POKEMON",
        hp: 70,
        hasArtwork: false,
        productCode: "TEST",
        collectorNumber: "001",
        chineseDetails: {
          sections: [{ kind: "招式", name: "测试招式", text: "抽一张牌。" }],
        },
      },
      {
        printingId: "CN:TEST:002",
        cnName: "测试能量",
        engineId: "E2",
        category: "ENERGY",
        hasArtwork: false,
        productCode: "TEST",
        collectorNumber: "002",
      },
    ];
    const state = () =>
      JSON.parse(
        localStorage.getItem("fixture-owner") ||
          '{"decks":[],"collection":{"version":0,"entries":[]}}',
      );
    const save = (value: any) =>
      localStorage.setItem("fixture-owner", JSON.stringify(value));
    window.__TAURI__ = {
      core: {
        invoke: async (command: string, args: any) => {
          if (command === "mobile_artwork")
            throw { code: "IMAGE_MISSING", message: "合成测试不下载卡图" };
          const request = args.request,
            s = state();
          (window as any).documentCalls ||= [];
          if (command === "mobile_files") {
            (window as any).documentCalls.push(request);
            if (localStorage.getItem("cancel-file")) return { cancelled: true };
            if (request.op === "pick-collection")
              return {
                text: "printingId,quantity,condition,notes,wishlist\r\nCN:TEST:001,2,良好,桌面导入,true\r\n",
              };
            if (request.op === "pick-deck")
              return {
                text:
                  localStorage.getItem("deck-file") ??
                  JSON.stringify({
                    schema: 1,
                    catalogVersion: s.catalogVersion || "test-version",
                    name: "文件卡组",
                    entries: [{ printingId: "CN:TEST:001", quantity: 2 }],
                  }),
              };
            if (
              request.op === "pick-catalog" ||
              request.op === "download-catalog"
            ) {
              s.candidate = {
                package: "a".repeat(64),
                base: "b".repeat(64),
                catalogVersion: "test-version-2",
                cards: 2,
                added: 1,
                removed: 0,
                engineMappingsChanged: 0,
                referencedMissing: [],
                canApply: true,
              };
              save(s);
              return s.candidate;
            }
            if (
              request.op === "share-deck" ||
              request.op === "share-collection"
            )
              return { shared: true };
            throw { message: "未知文件操作" };
          }
          if (command !== "mobile_request")
            throw new Error("Unexpected native command");
          switch (request.op) {
            case "capabilities":
              return {
                catalogVersion: s.catalogVersion || "test-version",
                implemented: {
                  localPractice: false,
                  localRuleVerification: false,
                },
              };
            case "artwork-settings":
              return {
                settings: s.source || {
                  source: "github-raw",
                  baseUrl: "",
                  capacityMiB: 512,
                },
                cachedBytes: 0,
              };
            case "catalog": {
              const matches = cards.filter(
                (c) =>
                  (!request.ownedOnly ||
                    s.collection.entries.some(
                      (r: any) =>
                        r.printingId === c.printingId &&
                        (r.quantity > 0 || r.wishlist),
                    )) &&
                  (!request.query || c.cnName.includes(request.query)),
              );
              return { cards: matches, total: matches.length };
            }
            case "card":
              return cards.find((c) => c.printingId === request.printingId);
            case "collection":
              return s.collection;
            case "prune-catalog-updates":
              return { removed: 1 };
            case "catalog-status":
              return {
                catalogVersion: s.catalogVersion || "test-version",
                pending: s.candidate || null,
              };
            case "apply-catalog":
              if (localStorage.getItem("block-update"))
                throw {
                  code: "UPDATE_BLOCKED_ONLINE",
                  message: "请先恢复待确认的对战操作，再应用目录更新",
                };
              s.catalogVersion = "test-version-2";
              s.candidate = null;
              save(s);
              return { applied: true, catalogVersion: s.catalogVersion };
            case "collection-batches":
              return s.batches || [];
            case "set-collection": {
              if (request.expectedVersion !== s.collection.version)
                throw { message: "收藏已变化，请刷新" };
              for (const row of request.changes)
                s.collection.entries = [
                  row,
                  ...s.collection.entries.filter(
                    (old: any) =>
                      old.printingId !== row.printingId ||
                      old.condition !== row.condition,
                  ),
                ];
              s.collection.version++;
              save(s);
              return s.collection;
            }
            case "preview-collection":
              return { previewHash: "p".repeat(64), rows: 1, quantity: 2 };
            case "import-collection": {
              s.beforeImport = structuredClone(s.collection.entries);
              s.collection.entries.push({
                printingId: "CN:TEST:001",
                quantity: 2,
                condition: "良好",
                notes: "桌面导入",
                wishlist: true,
              });
              s.collection.version++;
              s.batches = [
                {
                  id: "import-1",
                  quantity: 2,
                  createdAt: 1800000000,
                  status: "applied",
                },
              ];
              save(s);
              return {
                id: "import-1",
                duplicate: false,
                collection: s.collection,
              };
            }
            case "undo-collection":
              s.collection.entries = s.beforeImport;
              s.collection.version++;
              s.batches[0].status = "undone";
              save(s);
              return { collection: s.collection };
            case "export-collection":
              return {
                text: "printingId,quantity,condition,notes,wishlist\r\n",
                filename: "ptcg-collection.csv",
              };
            case "decks":
              return s.decks;
            case "delete-deck":
              if (
                s.decks.find((d: any) => d.id === request.id)?.version !==
                request.expectedVersion
              )
                throw { message: "卡组已变化，请重新加载后再删除" };
              s.decks = s.decks.filter((d: any) => d.id !== request.id);
              save(s);
              return { deleted: true };
            case "save-deck": {
              if (
                request.id &&
                s.decks.find((d: any) => d.id === request.id)?.version !==
                  request.expectedVersion
              )
                throw { message: "卡组已变化，请核对后重试" };
              const deck = {
                id: request.id || crypto.randomUUID(),
                name: request.name,
                entries: request.entries,
                version: (request.expectedVersion || 0) + 1,
              };
              s.decks = [deck, ...s.decks.filter((d: any) => d.id !== deck.id)];
              save(s);
              return deck;
            }
            case "set-holding":
              s.collection = {
                version: s.collection.version + 1,
                entries: [
                  {
                    printingId: request.printingId,
                    quantity: request.quantity,
                    wishlist: request.wishlist,
                    condition: "未标注",
                    notes: "",
                  },
                  ...s.collection.entries.filter(
                    (r: any) => r.printingId !== request.printingId,
                  ),
                ],
              };
              save(s);
              return s.collection;
            case "revisions":
              return { revisions: [] };
            case "freeze-deck":
              return {
                id: "frozen",
                validation: {
                  count: s.decks
                    .find((d: any) => d.id === request.id)
                    .entries.reduce((n: number, e: any) => n + e.quantity, 0),
                  structureReady: false,
                },
              };
            case "set-artwork-source":
              s.source = request.settings;
              save(s);
              return { settings: s.source, cachedBytes: 0 };
            case "export-deck": {
              const deck = s.decks.find((d: any) => d.id === request.id);
              if (request.format === "csv")
                return {
                  text:
                    "\uFEFFprintingId,quantity\r\n" +
                    deck.entries
                      .map((e: any) => `${e.printingId},${e.quantity}\r\n`)
                      .join(""),
                  filename: "ptcg-deck.csv",
                };
              if (request.format === "text")
                return {
                  text: deck.entries
                    .map((e: any) => `${e.quantity} ${e.printingId}`)
                    .join("\n"),
                  filename: "ptcg-deck.txt",
                };
              return {
                schema: 1,
                name: deck.name,
                catalogVersion: "test-version",
                entries: deck.entries,
              };
            }
            case "import-deck": {
              try {
                const text = request.text.replace(/^\uFEFF/, "").trim();
                const value = text.startsWith("{")
                  ? JSON.parse(text)
                  : {
                      name: "导入的卡组",
                      entries: text
                        .split(/\r?\n/)
                        .slice(text.includes(",") ? 1 : 0)
                        .filter(Boolean)
                        .map((line: string) => {
                          const parts = line.split(
                            text.includes(",") ? "," : /\s+/,
                          );
                          return text.includes(",")
                            ? {
                                printingId: parts[0],
                                quantity: Number(parts[1]),
                              }
                            : {
                                printingId: parts[1],
                                quantity: Number(parts[0]),
                              };
                        }),
                    };
                if (
                  value.entries.some(
                    (e: any) =>
                      !cards.some((c) => c.printingId === e.printingId),
                  )
                )
                  throw new Error();
                const deck = {
                  id: crypto.randomUUID(),
                  name: value.name,
                  entries: value.entries,
                  version: 1,
                };
                s.decks.unshift(deck);
                save(s);
                return deck;
              } catch {
                throw { message: "无法识别的卡牌版本" };
              }
            }
            default:
              throw new Error("Unexpected operation: " + request.op);
          }
        },
      },
    } as any;
  });
}
