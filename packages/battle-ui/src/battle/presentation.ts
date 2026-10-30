import type { PlayerSeat, CardInfo, EngineCard } from "./types";

/** Missing identity is never a catalog key. Keep exact IDs ahead of names. */
export function lookupCard(card: EngineCard, cards: Record<string, CardInfo>) {
  for (const key of [card.id, card.name])
    if (key?.trim() && Object.hasOwn(cards, key)) return cards[key];
  return undefined;
}

/** Callers order printings by display preference; later entries win. */
export function indexCards(cards: CardInfo[]): Record<string, CardInfo> {
  const entries = cards.flatMap((c) =>
    [c.englishName, ...(c.aliases || [])]
      .filter((key) => !!key?.trim())
      .map((key) => [key, c] as const),
  );
  return Object.fromEntries([
    ...entries,
    ...cards.filter((c) => !!c.engineId?.trim()).map((c) => [c.engineId, c] as const),
  ]);
}

/** Relative labels for both absolute seat IDs and the existing practice events. */
export function playerLabel(
  actor: string | null,
  viewerSeat: PlayerSeat,
  opponentLabel: string,
) {
  if (
    actor === "you" ||
    actor === "self" ||
    actor?.toUpperCase() === viewerSeat
  )
    return "你";
  if (
    actor === "ai" ||
    actor === "opponent" ||
    actor?.toUpperCase() === "PLAYER1" ||
    actor?.toUpperCase() === "PLAYER2"
  )
    return opponentLabel;
  return "系统";
}

/** Map only within the same effect's ordered printed skills; unknown text stays explicit. */
export function skillName(
  name: string,
  kind: "attack" | "ability",
  card?: EngineCard,
  info?: CardInfo,
) {
  if (!card || !info) return name;
  // A named TM's one granted attack is printed in the Tool rule section,
  // rather than the Pokémon skill sections used by the positional mapping.
  if (
    kind === "attack" && card.attacks?.length === 1 &&
    card.name === `Technical Machine: ${name}` &&
    (info.englishName === card.name || !!card.id && info.engineId === card.id) &&
    info.cnName.startsWith("招式学习器")
  ) return info.cnName.replace(/^招式学习器\s*[:：]?\s*/, "").trim() || name;
  const english =
    kind === "attack"
      ? card.attacks
      : card.abilities?.filter(
          (a) => !["Tera", "Ancient", "Future"].includes(a.name),
        );
  const chinese =
    kind === "attack" && info.attacks?.length
      ? info.attacks
      : info.chineseDetails?.sections?.filter(
          (s) => s.kind === (kind === "attack" ? "招式" : "特性"),
        );
  const index = english?.findIndex((a) => a.name === name) ?? -1;
  return index >= 0 && english?.length === chinese?.length
    ? chinese![index].name || name
    : name;
}

const eventCatalogs = new WeakMap<
  Record<string, CardInfo>,
  { names: Map<string, string>; pattern: RegExp | null }
>();

/** Localize only catalog-backed names; catalogs are replaced atomically by hosts. */
export function eventText(
  text: string,
  cards: Record<string, CardInfo>,
  sources: EngineCard[] = [],
) {
  let catalog = eventCatalogs.get(cards);
  if (!catalog) {
    const names = new Map<string, string>();
    for (const info of Object.values(cards))
      for (const alias of [info.englishName, ...(info.aliases || [])])
        if (alias && info.cnName) names.set(alias, info.cnName);
    const keys = [...names.keys()].sort((a, b) => b.length - a.length);
    const pattern = keys.length
      ? new RegExp(
          keys
            .map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
            .join("|"),
          "g",
        )
      : null;
    catalog = { names, pattern };
    eventCatalogs.set(cards, catalog);
  }
  let localized = text;
  for (const source of sources) {
    const info = lookupCard(source, cards);
    for (const [kind, skills] of [
      ["attack", source.attacks],
      ["ability", source.abilities],
    ] as const)
      for (const skill of skills || []) {
        const name = skillName(skill.name, kind, source, info);
        if (skill.name && name !== skill.name)
          localized = localized.split(skill.name).join(name);
      }
  }
  return catalog.pattern
    ? localized.replace(
        catalog.pattern,
        (name) => catalog.names.get(name) || name,
      )
    : localized;
}
