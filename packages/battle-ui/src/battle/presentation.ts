import type { PlayerSeat } from "./types";

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
