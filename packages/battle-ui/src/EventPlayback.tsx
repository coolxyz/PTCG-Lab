import { RetryImage } from "./RetryImage";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { BattleView, CardInfo, PlayerSeat } from "./battle/types";
import { playerLabel } from "./battle/presentation";
import "./battle/desktop-motion.css";

type Event = NonNullable<BattleView["events"]>[number];
type Impact = {
  kind: "charge" | "hit" | "heal" | "arrival" | "attach";
  style: CSSProperties;
};
// Presentation-only: this component has no command callback or network access.
export function EventPlayback({
  view,
  enabled,
  cards,
  opponentLabel = "对手",
  viewerSeat = "PLAYER1",
  mobile = false,
}: {
  view: BattleView;
  enabled: boolean;
  opponentLabel?: string;
  cards: Record<string, CardInfo>;
  viewerSeat?: PlayerSeat;
  mobile?: boolean;
}) {
  const sequence = view.matchSeq ?? view.stateVersion;
  const cursor = useRef({ matchId: view.matchId, seq: sequence });
  const [playing, setPlaying] = useState<Event>();
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [speed, setSpeed] = useState(1);
  const root = useRef<HTMLDivElement>(null);
  const [flights, setFlights] = useState<
    { name?: string | null; label: string; style: CSSProperties }[]
  >([]);
  const [damageLabels, setDamageLabels] = useState<
    { text: string; healing: boolean; style: CSSProperties }[]
  >([]);
  const [impacts, setImpacts] = useState<Impact[]>([]);
  const [strike, setStrike] = useState<CSSProperties | null>(null);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const previous = cursor.current;
    cursor.current = { matchId: view.matchId, seq: sequence };
    if (
      !enabled ||
      reduced ||
      previous.matchId !== view.matchId ||
      sequence < previous.seq
    ) {
      setPlaying(undefined);
      return;
    }
    if (previous.seq === sequence) return;
    const incoming = (view.events || [])
      .filter((e) => e.seq > previous.seq && e.seq <= sequence)
      .sort((a, b) => a.seq - b.seq);
    // Recovery gaps and long batches snap to the authoritative state.
    if (
      incoming.length !== sequence - previous.seq ||
      incoming.some((e, i) => e.seq !== previous.seq + i + 1) ||
      incoming.length > 8
    ) {
      setPlaying(undefined);
      return;
    }
    // The board already shows the newest snapshot. Do not aim earlier
    // animations at slots that may now contain different cards.
    const latest = incoming.at(-1);
    if (
      latest?.effects?.some(
        (effect) => effect.kind === "hp" || effect.kind === "move",
      ) ||
      (!mobile && latest?.actionType === "AttackAction")
    )
      setPlaying(latest);
  }, [view.matchId, sequence, view.events, enabled, mobile, reduced]);
  const event = enabled && !reduced ? playing : undefined;
  useEffect(() => {
    if (!event) return;
    const moves = event.effects?.filter((e) => e.kind === "move").length || 0;
    const duration = moves
      ? (mobile ? 840 : 1040) + Math.min(moves, 8) * 60
      : mobile
        ? 760
        : 1030;
    const timer = window.setTimeout(
      () => setPlaying(undefined),
      duration / speed,
    );
    return () => window.clearTimeout(timer);
  }, [event, mobile, speed]);
  useLayoutEffect(() => {
    const board = root.current?.closest(".tabletop");
    if (!event || !board) {
      setFlights([]);
      setDamageLabels([]);
      setImpacts([]);
      setStrike(null);
      return;
    }
    const bounds = board.getBoundingClientRect();
    const cardNodes = [
      ...board.querySelectorAll<HTMLElement>("[data-motion-card]"),
    ];
    const zoneNodes = [
      ...board.querySelectorAll<HTMLElement>("[data-motion-zone]"),
    ];
    const cardAt = (location: string, name?: string) =>
      cardNodes
        .find(
          (node) =>
            node.dataset.motionCard === location &&
            (!name || node.dataset.motionName === name),
        )
        ?.querySelector(".tabletop-card");
    function zoneAt(location: string) {
      // A closed zone has no box. Use a visible count/header when available.
      return zoneNodes.find(
        (node) =>
          node.dataset.motionZone === location && node.getClientRects().length,
      );
    }
    function center(element: Element | null | undefined) {
      const rect = element?.getBoundingClientRect();
      return rect && rect.width > 0 && rect.height > 0
        ? {
            left: rect.left - bounds.left + rect.width / 2,
            top: rect.top - bounds.top + rect.height / 2,
          }
        : undefined;
    }
    const cues: Impact[] = [];
    const actor = playerLabel(event.actor, viewerSeat, opponentLabel);
    const attackingSide =
      actor === "你"
        ? "self"
        : actor === opponentLabel
          ? "opponent"
          : undefined;
    let nextStrike: CSSProperties | null = null;
    const strikeTarget = event.effects?.find(
      (effect) =>
        effect.kind === "hp" &&
        effect.side !== attackingSide &&
        effect.after < effect.before,
    );
    if (event.actionType === "AttackAction" && attackingSide) {
      const sourceName = event.cardGroups?.[0]?.cards[0]?.name;
      const from = center(
        cardAt(
          event.active_pokemonLocation ?? `${attackingSide}:active:0`,
          sourceName,
        ),
      );
      if (!mobile && from) cues.push({ kind: "charge", style: from });
      const to =
        strikeTarget &&
        center(
          cardAt(
            `${strikeTarget.side}:${strikeTarget.zone}:${strikeTarget.index ?? 0}`,
          ),
        );
      if (from && to) {
        const dx = to.left - from.left;
        const dy = to.top - from.top;
        nextStrike = {
          ...from,
          "--strike-x": `${dx}px`,
          "--strike-y": `${dy}px`,
          "--strike-angle": `${Math.atan2(dy, dx)}rad`,
          "--strike-length": `${Math.max(44, Math.min(110, Math.hypot(dx, dy) * 0.3))}px`,
        } as CSSProperties;
      }
    }
    setStrike(nextStrike);
    setDamageLabels(
      (event.effects || [])
        .filter((e) => e.kind === "hp")
        .flatMap((e) => {
          const anchor = cardAt(`${e.side}:${e.zone}:${e.index ?? 0}`);
          if (!anchor) return [];
          const rect = anchor.getBoundingClientRect();
          const delta = e.after - e.before;
          if (!delta) return [];
          const impact = center(anchor);
          const delay =
            nextStrike && e === strikeTarget ? (mobile ? 220 : 440) / speed : 0;
          if (impact)
            cues.push({
              kind: delta < 0 ? "hit" : "heal",
              style: { ...impact, animationDelay: `${delay}ms` },
            });
          return [
            {
              text: `${delta > 0 ? "+" : ""}${delta} HP`,
              healing: delta > 0,
              style: {
                left: rect.left - bounds.left + rect.width / 2,
                top: rect.top - bounds.top - 14,
                animationDelay: `${delay}ms`,
              },
            },
          ];
        }),
    );
    const next = (event.effects || [])
      .filter((e) => e.kind === "move")
      .slice(0, 8)
      .flatMap((e, i) => {
        const start = center(zoneAt(`${e.side}:${e.fromZone}`));
        const field = e.zone === "active" || e.zone === "bench";
        const zone = zoneAt(`${e.side}:${e.zone}`);
        // Prefer a unique visible arrival; repeated names fall back to the zone.
        const arrivals =
          field && e.cardName
            ? cardNodes.filter(
                (node) =>
                  node.dataset.motionCard?.startsWith(`${e.side}:${e.zone}:`) &&
                  node.dataset.motionName === e.cardName,
              )
            : [];
        const end = center(
          arrivals.length === 1
            ? arrivals[0].querySelector(".tabletop-card")
            : zone,
        );
        if ((!mobile || field) && end)
          cues.push({
            kind: e.label === "附着" ? "attach" : "arrival",
            style: {
              ...end,
              animationDelay: `${((start ? (mobile ? 470 : 555) : 0) + i * 60) / speed}ms`,
            },
          });
        if (!start || !end) return [];
        return [
          {
            name: e.cardName,
            label: e.label || "移动",
            style: {
              left: start.left - 22,
              top: start.top - 31,
              "--fly-x": `${end.left - start.left}px`,
              "--fly-y": `${end.top - start.top}px`,
              animationDuration: `${600 / speed}ms`,
              animationDelay: `${(i * 60) / speed}ms`,
            } as CSSProperties,
          },
        ];
      });
    setFlights(next);
    setImpacts(cues);
    if (!mobile) return;
    const targets = (event.effects || [])
      .filter((e) => e.kind === "hp" || e.kind === "move")
      .flatMap((e) =>
        [...board.querySelectorAll<HTMLElement>("[data-motion-zone]")].filter(
          (a) => a.dataset.motionZone === `${e.side}:${e.zone}`,
        ),
      );
    targets.forEach((a) => a.classList.add("settlement-target"));
    const locations = [
      event.sourceLocation,
      event.targetLocation,
      event.active_pokemonLocation,
    ].filter(Boolean);
    const cards = [
      ...board.querySelectorAll<HTMLElement>("[data-motion-card]"),
    ].filter((a) => locations.includes(a.dataset.motionCard));
    const marker =
      event.actionType === "AttackAction"
        ? "settlement-attack"
        : "settlement-target";
    cards.forEach((a) => a.classList.add(marker));
    return () => {
      targets.forEach((a) => a.classList.remove("settlement-target"));
      cards.forEach((a) => a.classList.remove(marker));
    };
  }, [event, mobile, opponentLabel, speed, viewerSeat]);
  return (
    <>
      {event && (
        <div
          ref={root}
          className={`card-flight-layer${mobile ? "" : " desktop-battle-motion"}`}
          style={
            mobile
              ? undefined
              : ({ "--battle-motion-rate": speed } as CSSProperties)
          }
          aria-hidden="true"
          key={`flights-${event.seq}`}
        >
          {strike && <div className="battle-strike-trail" style={strike} />}
          {impacts.map((impact, i) => (
            <div
              key={i}
              className={`battle-impact battle-impact-${impact.kind}`}
              style={impact.style}
            />
          ))}
          {damageLabels.map((label, i) => (
            <div
              key={`hp-${i}`}
              className={`arena-hp-change battle-hp-float${label.healing ? " is-heal" : ""}`}
              style={label.style}
            >
              {label.text}
            </div>
          ))}
          {flights.map((flight, i) => (
            <div
              key={i}
              className={`card-flight ${flight.name ? "" : "cardback"}`}
              style={flight.style}
            >
              {flight.name && cards[flight.name]?.image.url ? (
                <RetryImage src={cards[flight.name].image.url!} alt="" />
              ) : (
                <span>
                  {flight.name
                    ? cards[flight.name]?.cnName || flight.name
                    : "卡背"}
                </span>
              )}
              <small>{flight.label}</small>
            </div>
          ))}
        </div>
      )}
      {!mobile && (
        <>
          <button disabled={!event} onClick={() => setPlaying(undefined)}>
            跳过反馈
          </button>
          <button
            disabled={!enabled || reduced}
            onClick={() => setSpeed(speed === 1 ? 2 : 1)}
            aria-label="动画速度"
            aria-pressed={speed === 2}
          >
            {speed}×
          </button>
        </>
      )}
    </>
  );
}
