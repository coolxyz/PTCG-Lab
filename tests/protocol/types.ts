import type { Command, MatchSnapshot, ReplayFrame, Seat, ProtocolVersion } from "@ptcg/battle-protocol";
import { protocolVersion } from "@ptcg/battle-protocol";

const version: ProtocolVersion = protocolVersion;
const seat: Seat = "PLAYER2";
const command: Command = {
  protocolVersion: version, commandId: "c1", expectedMatchSeq: 0,
  expectedStateVersion: 0, controllerEpoch: 1, decisionId: "d0",
  choice: { selectedRefs: ["d0:c0"] },
};
// @ts-expect-error incompatible protocol major
const badVersion: ProtocolVersion = "1.0";
// @ts-expect-error there is no spectator/player-supplied arbitrary seat
const badSeat: Seat = "PLAYER3";
// @ts-expect-error opponent hand must be null
const badHand: MatchSnapshot["observation"]["opponent"]["hand"] = [];
// @ts-expect-error replay frames have no executable decision
type ReplayDecision = ReplayFrame["decision"];
void [seat, command, badVersion, badSeat, badHand];
