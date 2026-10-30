import React, { useEffect, useState, useRef } from "react";
import { RefreshCw, Download, History, AlertCircle } from "lucide-react";
import "./sync.css";

const labels: Record<string, string> = {
  queued: "等待处理", running: "运行中", completed: "已完成", partial: "资料已发布 · 对战适配未全部完成",
  blocked: "需要处理", failed: "失败，可重试", cancelled: "已取消", ready: "预演完成", no_change: "没有变化",
  fetching: "获取固定上游提交", validating_source: "校验源数据", normalizing: "解析卡面信息", diffing: "计算增量",
  mapping: "映射卡牌身份", adapting: "适配环境与对战机制", testing: "验证候选资料", publishing: "发布中", published: "已发布",
  planned: "预演完成", checked: "检查完成", "supported-existing": "保留已有支持", "outside-environment": "不在当前环境或日期待确认",
  "source-review": "资料待核验", "reuse-candidate": "效果复用候选", "compiled-candidate": "规则编译候选", "implementation-required": "需要机制实现",
};

async function api(path: string, body?: unknown, method?: string) {
  const response = await fetch(`/api/sync${path}`, { method: method || (body === undefined ? "GET" : "POST"), headers: body === undefined ? {} : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "更新操作失败");
  return result;
}

export function Sync({ onPublished }: { onPublished: () => void }) {
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState("");
  const [repository, setRepository] = useState<string | null>(null);
  const [sourceSaved, setSourceSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resources, setResources] = useState<any>(null);
  const [artwork, setArtwork] = useState<any>(null);
  const [artworkDraft, setArtworkDraft] = useState<{source:string;baseUrl:string;capacityMiB:number} | null>(null);
  const [resourceFile, setResourceFile] = useState<File | null>(null);
  const [resourcePin, setResourcePin] = useState("");
  const resourceInput = useRef<HTMLInputElement>(null);
  const [tasks, setTasks] = useState<any[] | null>(null);
  const [inventory, setInventory] = useState<any>(null);
  async function refresh() {
    const [next, packs, images] = await Promise.all([api("/status"), api("/resources"), api("/artwork/settings")]);
    setStatus(next); setResources(packs); setArtwork(images);
  }
  useEffect(() => {
    let alive = true;
    const load = async () => { try { const [next, packs, images] = await Promise.all([api("/status"), api("/resources"), api("/artwork/settings")]); if (alive) { setStatus(next); setResources(packs); setArtwork(images); } } catch (e) { if (alive) setError(String(e)); } };
    void load(); const timer = setInterval(load, 4000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const running = busy || status?.jobs?.some((j: any) => ["queued", "running"].includes(j.state) || j.battleReleaseStatus === "testing");
  return <section className="workspace-page sync-page">
    <div className="page-heading"><div><div className="eyebrow">CATALOG & BATTLE UPDATES</div><h1>数据与对战更新</h1><p>跟踪你维护的卡库，保留收藏与卡组，逐项验证新卡的对战能力。</p></div></div>
    {error && <div className="error-banner" role="alert"><AlertCircle size={18} />{error}</div>}
    <div className="sync-source">
      <strong>上游卡牌资料仓库</strong>
      <label>GitHub 仓库地址<input aria-label="上游仓库地址" type="url" value={repository ?? status?.repository ?? ""}
        placeholder="https://github.com/duanxr/PTCG-CHS-Datasets"
        onChange={e => {setRepository(e.target.value); setSourceSaved(false);}} /></label>
      <button disabled={running || !status || repository === null} onClick={() => act(async () => {
        const saved = await api("/repository", {repository}); setRepository(saved.repository); setSourceSaved(true);
      })}>保存仓库地址</button>
      {sourceSaved && <span role="status">仓库地址已保存，下次更新使用此来源</span>}
      <span>main · 支持原仓库或相同数据格式的公开 GitHub fork · 手动同步</span>
      <a href={status?.repository || "https://github.com/duanxr/PTCG-CHS-Datasets"} target="_blank" rel="noreferrer">查看当前上游仓库 ↗</a>
      <a href="https://github.com/duanxr/PTCG-CHS-Datasets" target="_blank" rel="noreferrer">原仓库：duanxr/PTCG-CHS-Datasets ↗</a>
      <small>当前发布：{status?.currentRelease?.slice(0, 12) || "原有卡库"}　上次检查：{status?.lastCheck?.checkedAt ? new Date(status.lastCheck.checkedAt).toLocaleString() : "尚未检查"}</small>
    </div>
    <div className="sync-actions">
      <button disabled={running} onClick={() => act(() => api("/check", {}))}><RefreshCw size={17} />检查更新</button>
      <button disabled={running} onClick={() => act(() => api("/jobs", { kind: "plan" }))}>迁移预演</button>
      <button className="primary" disabled={running} onClick={() => act(() => api("/jobs", { kind: "sync" }))}><Download size={17} />同步并适配</button>
      <button onClick={onPublished}>刷新卡库视图</button>
    </div>
    <p className="sync-note">资料校验通过后发布。效果复用和编译候选仍需对战验收；未知机制保留开发任务，不会因下载完成而自动获得对战资格。</p>
    <details><summary>分配收藏版本</summary><p>原有数量保留为未分配版本。分配只细分现有持有数量，不增加卡组可用张数；减少总量前请先释放相应版本分配。</p><button onClick={() => act(async () => setInventory(await api("/variants")))}>读取收藏版本</button>
      {inventory?.entries?.map((entry: any) => <div key={entry.printingId + entry.condition} className="sync-release"><span>{entry.name} · {entry.condition} · 总量 {entry.quantity} · 未分配 {entry.unassigned}</span>{entry.variants.map((variant: any) => <label key={variant.variantId}>{variant.variantId} · {variant.rarity} · {variant.finishLabel}<input aria-label={`分配 ${entry.printingId} ${entry.condition} ${variant.variantId}`} type="number" min="0" max={entry.quantity} defaultValue={entry.allocations.find((a: any) => a.variantId === variant.variantId)?.quantity || 0} onBlur={event => { const quantity = Number(event.target.value); const old = entry.allocations.find((a: any) => a.variantId === variant.variantId)?.quantity || 0; if (quantity !== old) void act(async () => setInventory(await api("/variants", { printingId: entry.printingId, condition: entry.condition, variantId: variant.variantId, quantity, expectedVersion: inventory.version }))); }} /></label>)}</div>)}
    </details>
    <section className="sync-source" aria-label="展示资源管理">
      <h2>在线卡图与本机缓存</h2>
    <section className="sync-source" aria-label="在线卡图与本机缓存">
      <p>卡图在显示时按需下载，资料库、人机练习和网络对战共用本机缓存。已缓存的图片可离线查看。</p>
      <div className="sync-metrics" role="status">
        <span>内置图片索引 <b>{artwork?.indexedCards ?? "—"}</b></span>
        <span>已缓存图片 <b>{artwork?.cachedImages ?? "—"}</b></span>
        <span>缓存空间 <b>{artwork ? `${(artwork.cachedBytes / 1048576).toFixed(1)} MiB` : "—"}</b></span>
      </div>
      {!!artwork?.retryAfterSeconds && <p role="status">图片来源暂忙，正在退避；约 {artwork.retryAfterSeconds} 秒后可重试，已有图片仍可查看。</p>}
      <div className="sync-actions"><button disabled={busy || !!artwork?.retryAfterSeconds} onClick={onPublished}>重试显示卡图</button></div>
      <details className="sync-artwork-settings"><summary>图片来源与缓存设置</summary>
        <label>卡图来源<select aria-label="卡图来源" value={artworkDraft?.source ?? artwork?.source ?? "github-raw"}
          disabled={busy || !artwork} onChange={e => setArtworkDraft({source:e.target.value,baseUrl:artworkDraft?.baseUrl ?? artwork.baseUrl,capacityMiB:artworkDraft?.capacityMiB ?? artwork.capacityMiB})}>
          <option value="github-raw">GitHub Raw · 默认</option><option value="cdn">静态图源 / CDN</option>
        </select></label>
        {(artworkDraft?.source ?? artwork?.source) === "cdn" && <label>静态图源基础地址<input aria-label="静态图源基础地址" type="url" placeholder="https://images.example.org/ptcg"
          value={artworkDraft?.baseUrl ?? artwork?.baseUrl ?? ""} onChange={e => setArtworkDraft({source:artworkDraft?.source ?? artwork.source,baseUrl:e.target.value,capacityMiB:artworkDraft?.capacityMiB ?? artwork.capacityMiB})}/></label>}
        <label>本机缓存额度<select aria-label="本机缓存额度" disabled={busy || !artwork} value={artworkDraft?.capacityMiB ?? artwork?.capacityMiB ?? 1024}
          onChange={e => setArtworkDraft({source:artworkDraft?.source ?? artwork.source,baseUrl:artworkDraft?.baseUrl ?? artwork.baseUrl,capacityMiB:Number(e.target.value)})}>
          {[256,512,1024,2048,4096,8192].map(n=><option key={n} value={n}>{n >= 1024 ? `${n/1024} GiB` : `${n} MiB`}</option>)}
        </select></label>
        <p className="sync-note">空间不足时清理近期未使用的在线缓存；ZIP 离线资源和用户上传图片不受影响。GitHub Raw 也可能限流，失败时会暂停重试。</p>
        <p className="sync-note">静态图源按“基础地址 / 仓库所有者 / 仓库名 / 固定提交 / 图片路径”提供相同原图；仍会校验内容哈希。</p>
        <div className="sync-actions"><button className="primary" disabled={busy || !artworkDraft} onClick={() => act(async () => {
          await api("/artwork/settings", artworkDraft, "PUT"); setArtworkDraft(null); onPublished();
        })}>保存设置并刷新卡图</button><button disabled={busy || !artworkDraft} onClick={() => setArtworkDraft(null)}>取消修改</button></div>
      </details>
    </section>
    <h2>卡图与展示资源</h2>
      <p>安装已批准的 ZIP 并核对发布方摘要。展示包只更新卡图和文字；卡组身份与对战资格由规则决定。</p>
      <label>资源 ZIP<input aria-label="资源 ZIP" ref={resourceInput} type="file" accept=".zip,application/zip" disabled={running} onChange={e => setResourceFile(e.target.files?.[0] || null)} /></label>
      <label>发布方 SHA-256<input aria-label="资源 SHA-256" value={resourcePin} maxLength={64} spellCheck={false} onChange={e => setResourcePin(e.target.value.trim().toLowerCase())} placeholder="发布方提供的 64 位摘要" /></label>
      <button disabled={running || !resourceFile || !/^[a-f0-9]{64}$/.test(resourcePin)} onClick={() => act(async () => {
        const response = await fetch("/api/sync/resources/install", {method: "POST", headers: {"Content-Type": "application/zip", "X-PTCG-Resource-SHA256": resourcePin}, body: resourceFile});
        const result = await response.json(); if (!response.ok) throw new Error(result.message || "资源安装失败");
        setResourceFile(null); setResourcePin(""); if (resourceInput.current) resourceInput.current.value = "";
      })}>校验并安装资源</button>
      <p role="status">当前展示：{resources?.packs?.find((p: any) => p.id === resources.activePack)?.name || "内置展示"}。安装后可选择启用，既有版本保留供回放使用。</p>
      {resources?.activePack && <button disabled={running} onClick={() => act(async () => { await api("/resources/activate", {packId: null, expectedPack: resources.activePack}); onPublished(); })}>恢复内置展示</button>}
      {resources?.packs?.map((pack: any) => <div className="sync-release" key={pack.id}>
        <strong>{pack.name}</strong><small>{pack.id.slice(0,12)} · {(pack.bytes / 1048576).toFixed(1)} MiB</small>
        {pack.id === resources.activePack ? <span>使用中</span> : <button disabled={running} onClick={() => act(async () => { await api("/resources/activate", {packId: pack.id, expectedPack: resources.activePack}); onPublished(); })}>启用此展示</button>}
      </div>)}
    </section>
    <h2>更新批次</h2>
    {!status?.jobs?.length && <div className="sync-empty">尚无同步任务。先检查上游更新或运行迁移预演。</div>}
    {status?.jobs?.map((job: any) => <article className="sync-job" key={job.id}>
      <div className="sync-job-title"><strong>{labels[job.state] || job.state}</strong><span>{labels[job.stage] || job.stage}</span><time>{new Date(job.updatedAt).toLocaleString()}</time></div>
      <p>上游提交 {job.commit?.slice(0, 12) || "获取中"} · 环境日期 {job.asOf}</p>
      {job.report && <div className="sync-metrics"><span>目录身份 <b>{job.report.after}</b></span><span>新增身份 <b>{job.report.added}</b></span><span>收藏版本 <b>{job.report.variants}</b></span><span>身份冲突 <b>{job.report.conflicts}</b></span></div>}
      {job.statuses && <ul>{Object.entries(job.statuses).map(([key, value]) => <li key={key}>{labels[key] || key}：{String(value)}</li>)}</ul>}
      {job.error && <p role="alert" className="sync-failure">{job.error.message}（{job.error.code}）</p>}
      {job.battleReleaseStatus && <p>对战验证：{({ testing: "正在执行 1,000 局验收", "simulation-verified": "对局验收通过，发布仍需完整回归", "smoke-only": "仅通过小规模验证", failed: "验证失败", "published-partial": "已发布已验证效果，仍有待实现机制", pending: "等待验证", partial: "仍有待适配效果" } as Record<string, string>)[job.battleReleaseStatus] || job.battleReleaseStatus}</p>}
      <div className="sync-actions">
        {!!job.proposals && <button disabled={running} onClick={() => act(() => api(`/jobs/${job.id}/verify-battle`, {}))}>验证候选对战效果</button>}
        {job.battleReleaseStatus === "simulation-verified" && <button disabled={running} onClick={() => act(() => api(`/jobs/${job.id}/publish-battle`, { expectedRelease: status.currentRelease }))}>验收并发布对战效果</button>}
        {!!job.tasks && <button onClick={() => act(async () => setTasks(await api(`/jobs/${job.id}/tasks`)))}>查看待实现机制（{job.tasks}）</button>}
        {["failed", "blocked", "cancelled"].includes(job.state) && <button disabled={running} onClick={() => act(() => api(`/jobs/${job.id}/retry`, {}))}>重试</button>}
        {(["queued", "running"].includes(job.state) || job.battleReleaseStatus === "testing") && <button onClick={() => act(() => api(`/jobs/${job.id}/cancel`, {}))}>取消</button>}
      </div>
    </article>)}
    {tasks && <section className="sync-tasks"><div className="sync-job-title"><h2>机制开发任务</h2><button onClick={() => setTasks(null)}>关闭</button></div><p>共 {tasks.length} 个任务。相同完整卡面规则合并为一个任务，开发验收后再发布对战支持。</p>{tasks.slice(0, 30).map(t => <details key={t.id}><summary>{t.face?.name} · {t.cardIds.length} 个版本</summary><pre>{JSON.stringify(t, null, 2)}</pre></details>)}{tasks.length > 30 && <p>此处展示前 30 项；完整任务可通过该批次 tasks API 导出。</p>}</section>}
    <h2><History size={19} />发布历史</h2>
    {status?.releases?.map((r: any) => <div className="sync-release" key={r.releaseId}><span>{r.releaseId.slice(0, 12)} · {r.metadata.kind === "baseline" ? "迁移前基线" : r.metadata.kind === "battle" ? "对战发布" : "资料发布"}</span><small>{new Date(r.createdAt).toLocaleString()}</small>{status.currentRelease !== r.releaseId && <button disabled={running || !status.currentRelease} onClick={() => act(() => api("/rollback", { releaseId: r.releaseId, expectedRelease: status.currentRelease }))}>回滚到此版本</button>}</div>)}
  </section>;
}
