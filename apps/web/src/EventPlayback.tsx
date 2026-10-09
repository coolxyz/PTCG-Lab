import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { BattleView, CardInfo } from "./Battle";

type Event = NonNullable<BattleView["events"]>[number];
const zones: Record<string, string> = {
  hand: "手牌",
  deck: "牌库",
  prize: "奖赏",
  active: "战斗区",
  bench: "备战区",
  discard: "弃牌区",
  lost_zone: "放逐区",
};

// Presentation-only: this component has no command callback or network access.
export function EventPlayback({
  view,
  enabled,
  cards,
}: {
  view: BattleView;
  enabled: boolean;
  cards: Record<string, CardInfo>;
}) {
  const cursor = useRef({ matchId: view.matchId, seq: view.stateVersion });
  const [queue, setQueue] = useState<Event[]>([]);
  const [reduced, setReduced] = useState(false);
  const [speed, setSpeed] = useState(1);
  const root = useRef<HTMLElement>(null);
  const [flights, setFlights] = useState<
    { name?: string | null; label: string; style: CSSProperties }[]
  >([]);
  const [damageLabels, setDamageLabels] = useState<{text: string; style: CSSProperties}[]>([]);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const previous = cursor.current;
    cursor.current = { matchId: view.matchId, seq: view.stateVersion };
    if (
      !enabled ||
      reduced ||
      previous.matchId !== view.matchId ||
      view.stateVersion < previous.seq
    ) {
      setQueue([]);
      return;
    }
    if (previous.seq === view.stateVersion) return;
    const incoming = (view.events || [])
      .filter((e) => e.seq > previous.seq && e.seq <= view.stateVersion)
      .sort((a, b) => a.seq - b.seq);
    // Recovery gaps and long batches snap to the authoritative state.
    if (
      incoming.length !== view.stateVersion - previous.seq ||
      incoming.some((e, i) => e.seq !== previous.seq + i + 1) ||
      incoming.length > 8
    ) {
      setQueue([]);
      return;
    }
    setQueue((old) =>
      old.length + incoming.length > 8 ? [] : [...old, ...incoming],
    );
  }, [view.matchId, view.stateVersion, view.events, enabled, reduced]);
  useEffect(() => {
    if (!queue.length) return;
    const moves =
      queue[0].effects?.filter((e) => e.kind === "move").length || 0;
    const timer = window.setTimeout(
      () => setQueue((old) => old.slice(1)),
      (moves ? 650 + Math.min(moves, 8) * 60 : 450) / speed,
    );
    return () => window.clearTimeout(timer);
  }, [queue, speed]);
  const event = queue[0];
  useLayoutEffect(() => {
    const board = root.current?.closest(".tabletop");
    if (!event || !board) {
      setFlights([]);
      setDamageLabels([]);
      return;
    }
    const bounds = board.getBoundingClientRect();
    setDamageLabels((event.effects || []).filter(e => e.kind === "hp").flatMap(e => {
      const anchor = [...board.querySelectorAll<HTMLElement>("[data-motion-card]")].find(a => a.dataset.motionCard === `${e.side}:${e.zone}:${e.index}`);
      if (!anchor) return [];
      const rect = anchor.getBoundingClientRect();
      const delta = e.after - e.before;
      return [{text: `${delta > 0 ? "+" : ""}${delta} HP`, style: {left: rect.left - bounds.left + rect.width / 2, top: rect.top - bounds.top - 14}}];
    }));
    const next = (event.effects || [])
      .filter((e) => e.kind === "move")
      .slice(0, 8)
      .flatMap((e, i) => {
        // Enumerating known DOM attributes avoids building selectors from payloads.
        const anchors = [
          ...board.querySelectorAll<HTMLElement>("[data-motion-zone]"),
        ];
        const start = anchors
          .find((a) => a.dataset.motionZone === `${e.side}:${e.fromZone}`)
          ?.getBoundingClientRect();
        const end = anchors
          .find((a) => a.dataset.motionZone === `${e.side}:${e.zone}`)
          ?.getBoundingClientRect();
        if (!start || !end) return [];
        return [
          {
            name: e.cardName,
            label: e.label || "移动",
            style: {
              left: start.left + start.width / 2 - bounds.left - 22,
              top: start.top + start.height / 2 - bounds.top - 31,
              "--fly-x": `${end.left + end.width / 2 - start.left - start.width / 2}px`,
              "--fly-y": `${end.top + end.height / 2 - start.top - start.height / 2}px`,
              animationDuration: `${600 / speed}ms`,
              animationDelay: `${(i * 60) / speed}ms`,
            } as CSSProperties,
          },
        ];
      });
    setFlights(next);
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
  }, [event, speed]);
  if (!event) return null;
  return (
    <>
      <div
        className="card-flight-layer"
        aria-hidden="true"
        key={`flights-${event.seq}`}
      >
        {damageLabels.map((label, i) => <div key={`hp-${i}`} className="arena-hp-change" style={label.style}>{label.text}</div>)}
        {flights.map((flight, i) => (
          <div
            key={i}
            className={`card-flight ${flight.name ? "" : "cardback"}`}
            style={flight.style}
          >
            {flight.name && cards[flight.name]?.image.url ? (
              <img src={cards[flight.name].image.url!} alt="" />
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
      <section ref={root} className="event-playback" aria-label="结算反馈">
        <div key={event.seq} className="event-playback-step">
          <strong>
            第 {event.seq} 步 · {event.text}
          </strong>
          <div>
            {event.effects?.map((effect, i) => (
              <span key={i} className={`event-delta ${effect.kind}`}>
                {effect.kind === "move" ? (
                  <>
                    {effect.side === "self" ? "你" : "AI"} · {effect.label}：
                    {effect.cardName
                      ? cards[effect.cardName]?.cnName || effect.cardName
                      : "卡背"}{" "}
                    · {zones[effect.fromZone || ""]} → {zones[effect.zone]}
                  </>
                ) : effect.kind === "turn" ? (
                  <>换回合 · {effect.after}</>
                ) : (
                  <>
                    {effect.side === "self" ? "你" : "AI"} ·{" "}
                    {zones[effect.zone] || effect.zone}
                    {effect.index !== undefined ? ` ${effect.index + 1}` : ""}
                    {effect.kind === "hp" ? " HP" : ""}：{effect.before} →{" "}
                    {effect.after}
                  </>
                )}
              </span>
            ))}
          </div>
        </div>
        <button onClick={() => setQueue([])}>跳过反馈</button>
        <button
          onClick={() => setSpeed(speed === 1 ? 2 : 1)}
          aria-label="动画速度"
        >
          {speed}×
        </button>
      </section>
    </>
  );
}
