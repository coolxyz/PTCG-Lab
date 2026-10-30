import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { BattleArena, type CardInfo } from "@ptcg/battle-ui";
import { PracticeController } from "./controller";
import { practiceAPI } from "./transport";
import { browserPracticeStorage } from "./storage";
import "./practice.css";
import { PracticeHistory } from "./PracticeHistory";
import { browserAppearanceStorage } from "../appearance-storage";
import { indexCards } from "@ptcg/battle-ui/presentation";

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
      indexCards(
        [...(state.session?.cards ?? [])]
          .filter((c) => c.effectStatus === "verified")
          .sort((a, b) => {
            const score = (c: CardInfo) =>
              (c.image.userUploaded ? 100 : c.image.url ? 10 : 0) +
              (c.chineseDetails?.sections.length ? 1 : 0);
            return score(a) - score(b);
          }),
      ),
    [state.session],
  );
  if (!active) return null;
  if (state.view && state.showing)
    return (
      <BattleArena
        appearanceStorage={browserAppearanceStorage}
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
  const playable = state.revisions.filter((r) => r.playable);
  const selected = state.revisions.find((r) => r.id === state.revision);
  const strategy =
    state.aiLevel === "H2"
      ? "进阶 AI"
      : state.aiLevel === "A1"
        ? "基础 AI"
        : "实验搜索";
  return (
    <section className="workspace-page practice-lobby">
      <header className="page-heading">
        <div>
          <div className="eyebrow">PRACTICE AT YOUR OWN PACE</div>
          <h1>人机练习</h1>
          <p>带上你的卡组，尝试新的对战思路。随时暂停、继续或逐步回放。</p>
        </div>
        <button
          className="workspace-button is-secondary"
          disabled={state.busy}
          onClick={controller.refresh}
        >
          刷新大厅
        </button>
      </header>
      {state.error && (
        <p className="practice-notice is-error" role="alert">
          {state.error}
        </p>
      )}
      {!state.initialized && (
        <p className="practice-notice" role="status">
          正在连接本地练习服务…
        </p>
      )}
      {state.creation && (
        <p className="practice-notice" role="status">
          上次开局结果尚未确认，请重试原请求。卡组与策略会保持原选择。
        </p>
      )}
      {state.view && (
        <div className="practice-resume">
          <div>
            <strong>
              {state.view.done ? "上次练习已结束" : "你有一场未完成的练习"}
            </strong>
            <p>
              {state.view.done
                ? "查看对局结果，或从第一步重温对战。"
                : "当前进度已保留，可以继续对战，也可以开始新的练习。"}
            </p>
          </div>
          <button
            className="workspace-button is-secondary"
            disabled={state.busy}
            onClick={
              state.view.done
                ? () => void controller.openReplay(state.view!.matchId)
                : controller.resume
            }
          >
            {state.view.done ? "回看上次对局" : "返回当前对局"}
          </button>
        </div>
      )}
      <div className="practice-overview" aria-label="练习概览">
        <div>
          <span>可对战的卡组版本</span>
          <strong>
            {playable.length}
            <small> 个</small>
          </strong>
        </div>
        <div>
          <span>本次对手策略</span>
          <strong>
            {strategy}
            <small>{state.aiLevel === "H2" ? " · 推荐" : ""}</small>
          </strong>
        </div>
        <div>
          <span>已保存的练习</span>
          <strong>
            {state.matches.length}
            <small> 场</small>
          </strong>
        </div>
      </div>
      <section className="practice-setup" aria-label="开始一局">
        <div className="practice-setup-grid">
          <div className="practice-panel">
            <div className="practice-panel-heading">
              <span className="practice-step">01 / 准备</span>
              <h2>选择我的卡组</h2>
              <p>与构筑、网络对战共用平台中已保存的卡组版本。</p>
            </div>
            <label>
              我的卡组版本
              <select
                aria-label="对战卡组版本"
                value={state.revision}
                disabled={locked || !state.initialized}
                onChange={(e) => controller.select("revision", e.target.value)}
              >
                <option value="">请选择版本</option>
                {state.revisions.map((r) => (
                  <option key={r.id} value={r.id} disabled={!r.playable}>
                    {r.name} · 版本 {r.number}
                    {r.playable
                      ? ""
                      : `（${r.reason || "当前规则下不可对战"}）`}
                  </option>
                ))}
              </select>
            </label>
            {!playable.length && state.initialized && (
              <p className="practice-help">
                还没有可对战的版本。可以先使用推荐预设，或前往构筑保存卡组版本。
              </p>
            )}
            <div className="practice-preset">
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
              <p className="practice-help">
                还没准备好卡组？从推荐预设开始，之后也能继续编辑。
              </p>
            </div>
            <button className="practice-text-button" onClick={onBuild}>
              前往构筑并保存版本 →
            </button>
          </div>
          <div className="practice-panel">
            <div className="practice-panel-heading">
              <span className="practice-step">02 / 对手</span>
              <h2>设置练习对手</h2>
              <p>选择对手的卡组和策略，安排适合自己的练习。</p>
            </div>
            <label>
              AI 卡组
              <select
                aria-label="AI 卡组"
                value={state.opponent}
                disabled={locked || !state.initialized}
                onChange={(e) => controller.select("opponent", e.target.value)}
              >
                <option value="" disabled>
                  请选择对手卡组
                </option>
                <optgroup label="我的卡组版本">
                  {playable.map((r) => (
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
                disabled={locked || !state.initialized}
                onChange={(e) => controller.select("aiLevel", e.target.value)}
              >
                <option value="A1">基础 AI（A1）</option>
                <option value="H2">进阶 AI（H2 · 推荐）</option>
                <option value="A2">A2 · 实验搜索（强度未评级）</option>
              </select>
            </label>
            <div className="practice-strategy">
              <span aria-hidden="true">◎</span>
              <div>
                <strong>{strategy}</strong>
                <p>
                  {state.aiLevel === "H2"
                    ? "适合熟悉卡组配合与练习对战决策。"
                    : state.aiLevel === "A1"
                      ? "适合熟悉基本规则与卡组操作。"
                      : "尝试实验搜索策略，当前强度尚未评级。"}
                </p>
              </div>
            </div>
          </div>
        </div>
        <div className="practice-start">
          <div>
            <strong>
              {selected
                ? `${selected.name} · 版本 ${selected.number}`
                : "选择卡组后即可开始"}
            </strong>
            <p>对局在本机保存，支持暂停 AI、单步操作与完整回放。</p>
          </div>
          <button
            className="workspace-button"
            disabled={
              state.busy ||
              !state.initialized ||
              (!state.creation && (!state.revision || !state.opponent))
            }
            onClick={controller.create}
          >
            {state.creation ? "重试开局" : "开始人机对战"}
            <span aria-hidden="true"> →</span>
          </button>
        </div>
      </section>
      <PracticeHistory state={state} controller={controller} />
    </section>
  );
}
