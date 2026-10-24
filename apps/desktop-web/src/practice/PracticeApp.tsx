import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { BattleArena, type CardInfo } from "@ptcg/battle-ui";
import { PracticeController } from "./controller";
import { practiceAPI } from "./transport";
import { browserPracticeStorage } from "./storage";
import "./practice.css";

export function PracticeApp({
  active,
  onBuild,
}: {
  active: boolean;
  onBuild: () => void;
}) {
  const [controller] = useState(
    () => new PracticeController(practiceAPI(), browserPracticeStorage()),
  );
  const state = useSyncExternalStore(controller.subscribe, controller.snapshot);
  useEffect(() => {
    controller.setActive(active);
    return () => controller.setActive(false);
  }, [controller, active]);
  const names = useMemo(
    () =>
      Object.fromEntries(
        [...(state.session?.cards ?? [])]
          .filter((c) => c.effectStatus === "verified")
          .sort((a, b) => {
            const score = (c: CardInfo) =>
              (c.image.userUploaded ? 100 : c.image.url ? 10 : 0) +
              (c.chineseDetails?.sections.length ? 1 : 0);
            return score(a) - score(b);
          })
          .flatMap((c) => [
            [c.englishName, c],
            [c.engineId, c],
          ]),
      ),
    [state.session],
  );
  if (!active) return null;
  if (state.view && state.showing)
    return (
      <BattleArena
        view={state.view}
        cards={names}
        viewerSeat="PLAYER1"
        opponentLabel="AI"
        busy={state.busy}
        error={state.error}
        pendingCommand={!!state.pending}
        onSubmit={controller.submit}
        onRetry={controller.retry}
        onRefresh={controller.refresh}
        onLeave={controller.leave}
        onOpenReplay={state.pending ? undefined : () => void controller.seek(0)}
        onResign={state.pending ? undefined : controller.resign}
        onCoinPlaying={controller.setCoinPlaying}
        statusLabel={
          state.view.aiStrategy && (
            <span aria-label="当前 AI 策略">{state.view.aiStrategy.name}</span>
          )
        }
        modeControls={
          <div className="ai-playback-controls">
            <label>
              AI 行动速度{" "}
              <select
                aria-label="AI 行动速度"
                value={state.aiDelay}
                onChange={(e) => controller.delay(Number(e.target.value))}
              >
                <option value={3000}>慢速 · 3 秒</option>
                <option value={1800}>标准 · 1.8 秒</option>
                <option value={1200}>快速 · 1.2 秒</option>
              </select>
            </label>
            <button
              className="secondary"
              disabled={state.view.done}
              aria-pressed={state.paused}
              onClick={controller.pause}
            >
              {state.paused ? "继续 AI" : "暂停 AI"}
            </button>
            {state.paused && (
              <button
                className="secondary"
                disabled={
                  state.busy ||
                  !!state.view.decision ||
                  !!state.pending ||
                  state.view.done ||
                  state.coinPlaying
                }
                onClick={controller.advance}
              >
                AI 下一步
              </button>
            )}
          </div>
        }
        replay={
          state.frame
            ? {
                frame: state.frame,
                seq: state.seq,
                end: state.end,
                timeline: state.timeline,
                playing: state.playing,
                speed: state.speed,
                animate: state.animate,
                onSeek: (seq) => void controller.seek(seq),
                onPlayingChange: controller.play,
                onSpeedChange: controller.speed,
                onClose: controller.closeReplay,
              }
            : undefined
        }
      />
    );
  const locked = state.busy || !!state.creation;
  return (
    <main className="practice-lobby">
      <header>
        <h1>人机练习</h1>
        <p>H2 进阶对战 · 五套推荐预设 · 本地会话</p>
        <button disabled={state.busy} onClick={controller.refresh}>
          刷新大厅
        </button>
      </header>
      {state.error && <p role="alert">{state.error}</p>}
      {!state.initialized && <p role="status">正在连接本地练习服务…</p>}
      {state.creation && (
        <p role="status">上次开局结果尚未确认，请重试原请求。</p>
      )}
      {state.view && <button onClick={controller.resume}>返回当前对局</button>}
      <section aria-label="开始一局">
        <label>
          快速使用推荐预设
          <select
            aria-label="我的预设卡组"
            value=""
            disabled={locked || !state.initialized}
            onChange={(e) =>
              e.target.value && void controller.preset(e.target.value)
            }
          >
            <option value="">选择预设并保存为我的卡组版本</option>
            {state.session?.presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          我的卡组版本
          <select
            aria-label="对战卡组版本"
            value={state.revision}
            disabled={locked}
            onChange={(e) => controller.select("revision", e.target.value)}
          >
            <option value="">请选择版本</option>
            {state.revisions.map((r) => (
              <option key={r.id} value={r.id} disabled={!r.playable}>
                {r.name} · 版本 {r.number}
                {r.playable ? "" : `（${r.reason || "当前规则下不可对战"}）`}
              </option>
            ))}
          </select>
        </label>
        <label>
          AI 卡组
          <select
            aria-label="AI 卡组"
            value={state.opponent}
            disabled={locked}
            onChange={(e) => controller.select("opponent", e.target.value)}
          >
            <optgroup label="我的卡组版本">
              {state.revisions
                .filter((r) => r.playable)
                .map((r) => (
                  <option key={r.id} value={"revision:" + r.id}>
                    {r.name} · 版本 {r.number}
                  </option>
                ))}
            </optgroup>
            {state.session?.opponents.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          AI 策略
          <select
            aria-label="AI 策略"
            value={state.aiLevel}
            disabled={locked}
            onChange={(e) => controller.select("aiLevel", e.target.value)}
          >
            <option value="A1">基础 AI（A1）</option>
            <option value="H2">进阶 AI（H2 · 推荐）</option>
            <option value="A2">A2 · 实验搜索（强度未评级）</option>
          </select>
        </label>
        <button
          disabled={
            state.busy ||
            !state.initialized ||
            (!state.creation && (!state.revision || !state.opponent))
          }
          onClick={controller.create}
        >
          {state.creation ? "重试开局" : "开始人机对战"}
        </button>
        <button onClick={onBuild}>前往构筑并保存版本</button>
        <p>策略匹配由本地服务根据卡表和规则环境决定；界面不更改 AI 策略。</p>
      </section>
      <section aria-label="我的对局">
        <h2>我的对局</h2>
        {!state.matches.length && <p>还没有对局</p>}
        {state.matches.map((m) => (
          <button
            key={m.id}
            disabled={state.busy}
            onClick={() => void controller.open(m.id)}
          >
            {state.session?.opponents.find((o) => o.id === m.opponent)?.name ||
              state.revisions.find((r) => "revision:" + r.id === m.opponent)
                ?.name ||
              "历史 AI 卡组"}{" "}
            · {m.status === "playing" ? "继续对局" : "查看结果 / 回放"} ·{" "}
            {m.seq} 步
          </button>
        ))}
      </section>
    </main>
  );
}
