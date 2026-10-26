import { useEffect, useState } from "react";
import { BattleArena, type Command, type PlayerSeat } from "@ptcg/battle-ui";
import {
  demoSnapshot,
  demoReplay,
  scenarios,
  type Scenario,
  demoPublicHistory,
  demoStadiumHistory,
} from "./demo-data";
import { snapshotView, replayView } from "./presentation";
import "./demo.css";
import { browserAppearanceStorage } from "./appearance-storage";
import { visibleHistory } from "./online/history";

export function DemoApp() {
  const [seat, setSeat] = useState<PlayerSeat>("PLAYER1");
  const [scenario, setScenario] = useState<Scenario>("options");
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState(() => demoSnapshot());
  const [lastChoice, setLastChoice] = useState<Command["choice"] | null>(null);
  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [historyCount, setHistoryCount] = useState(3);
  const history =
    scenario === "stadium-history" ? demoStadiumHistory(seat, historyCount)
      : scenario === "public-history" || scenario === "legacy-history"
      ? demoPublicHistory(seat, historyCount).map((frame) =>
          scenario === "legacy-history" ? { ...frame, events: [] } : frame,
        )
      : null;
  const page = demoReplay(seat);
  // Playback timing belongs to this mode host, never the shared display package.
  useEffect(() => {
    if (
      !open ||
      !playing ||
      replaySeq === null ||
      replaySeq >= page.frames.length - 1
    ) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(
      () => setReplaySeq((n) => (n === null ? n : n + 1)),
      900 / speed,
    );
    return () => window.clearTimeout(timer);
  }, [open, playing, replaySeq, speed, page.frames.length]);
  function choose(nextSeat: PlayerSeat, nextScenario: Scenario) {
    setSeat(nextSeat);
    setScenario(nextScenario);
    setSnapshot(demoSnapshot(nextSeat, nextScenario));
    setLastChoice(null);
    setReplaySeq(null);
    setPlaying(false);
    setHistoryCount(3);
  }
  return (
    <>
      {!open && (
        <main className="demo-lobby">
          <h1>对战界面演示</h1>
          <p role="status">
            网络模式尚未连接服务器。这里仅展示合成局面，不创建真实房间或对局。
          </p>
          <label>
            座位
            <select
              value={seat}
              onChange={(e) => choose(e.target.value as PlayerSeat, scenario)}
            >
              <option>PLAYER1</option>
              <option>PLAYER2</option>
            </select>
          </label>
          <label>
            演示场景
            <select
              value={scenario}
              onChange={(e) => choose(seat, e.target.value as Scenario)}
            >
              {scenarios.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <button onClick={() => setOpen(true)}>打开对战界面</button>
          <output aria-label="演示选择回调">
            {lastChoice ? JSON.stringify(lastChoice) : "尚未选择"}
          </output>
        </main>
      )}
      {open && (
        <BattleArena
          appearanceStorage={browserAppearanceStorage}
          view={
            history
              ? {
                  ...snapshotView(history.at(-1)!),
                  events: visibleHistory(history, {}),
                }
              : snapshotView(snapshot)
          }
          cards={{}}
          viewerSeat={seat}
          opponentLabel="对手"
          statusLabel={<span role="status">合成演示 · 尚未连接服务器</span>}
          onSubmit={(choice) => setLastChoice(choice)}
          onRefresh={() =>
            history
              ? setHistoryCount((n) => n + 1)
              : setSnapshot((value) => structuredClone(value))
          }
          onLeave={() => {
            setOpen(false);
            setPlaying(false);
          }}
          onOpenReplay={() => {
            setReplaySeq(0);
            setPlaying(false);
          }}
          replay={
            replaySeq === null
              ? undefined
              : {
                  frame: replayView(page, replaySeq),
                  seq: replaySeq,
                  end: page.frames.length - 1,
                  timeline: [
                    { seq: 0, turn: 1, player: seat, phase: "playing" },
                  ],
                  playing,
                  speed,
                  animate: true,
                  onSeek: (seq) => {
                    setReplaySeq(seq);
                    setPlaying(false);
                  },
                  onPlayingChange: setPlaying,
                  onSpeedChange: setSpeed,
                  onClose: () => {
                    setReplaySeq(null);
                    setPlaying(false);
                  },
                }
          }
        />
      )}
    </>
  );
}
