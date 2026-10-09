import { useEffect, useState } from 'react';
import { DetailImage } from './DetailImage';

async function request(path: string, body?: unknown) {
  const response=await fetch('/api/sync/variants'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});
  const data=await response.json();
  if(!response.ok) throw new Error(data.message || '读取卡面失败');
  return data;
}

export function CardVariants({id,version,onChange}:{id:string;version:number;onChange:()=>Promise<void>}) {
  const [data,setData]=useState<any>(null),[condition,setCondition]=useState('未标注');
  const [values,setValues]=useState<Record<string,number>>({}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{let active=true;setError('');request('/cards/'+encodeURIComponent(id)).then(d=>{if(active)setData(d)}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[id,version]);
  useEffect(()=>{const entry=data?.entries.find((e:any)=>e.condition===condition);setValues(Object.fromEntries((entry?.allocations || []).map((a:any)=>[a.variantId,a.quantity])))},[data,condition]);
  async function save(variantId:string,allocate=false){
    setBusy(true);setError('');
    try {
      await request(allocate?'':'/holding',{printingId:id,condition,variantId,quantity:values[variantId] || 0,expectedVersion:data.version});
      setData(await request('/cards/'+encodeURIComponent(id)));
      await onChange();
    } catch(e){setError((e as Error).message)} finally{setBusy(false)}
  }
  const entry=data?.entries.find((e:any)=>e.condition===condition);
  return <section className="card-variants" aria-label="卡面版本与收藏">
    <h3>卡面版本与收藏 {data && <small>· {data.variants.length} 个版本</small>}</h3>
    {error && <p role="alert">{error}</p>}
    {!data ? <p>正在读取卡面…</p> : <>
      <label>品相 <select aria-label="卡面收藏品相" value={condition} disabled={busy} onChange={e=>setCondition(e.target.value)}>{['未标注','全新','良好','使用痕迹'].map(c=><option key={c}>{c}</option>)}</select></label>
      <p>此品相共 {entry?.quantity || 0} 张 · 未分配卡面 {entry?.unassigned || 0} 张</p>
      <p className="muted">“保存卡面数量”会同步增减持有总量。整理以前登记的收藏时，请使用“分配已有收藏”，总量保持不变。</p>
      <div className="variant-grid">{data.variants.map((v:any)=><article key={v.variantId} className="variant-card" data-variant={v.variantId}>
        {v.image?.url ? <DetailImage compact card={{cnName: v.finishLabel+' · CHS:'+v.upstreamId, image: {...v.image, label: v.rarity || '卡面'}}} onError={e=>{e.currentTarget.hidden=true}} /> : <p>暂无此版本卡图</p>}
        <b>{v.finishLabel}</b><small>{v.rarity || '无稀有度标记'} · CHS:{v.upstreamId}{v.removed?' · 上游已移除':''}</small>
        <label>持有数量<input aria-label={'卡面数量 '+v.upstreamId} type="number" min="0" max="9999" disabled={busy} value={values[v.variantId] || 0} onChange={e=>setValues({...values,[v.variantId]:Number(e.target.value)})}/></label>
        <button disabled={busy} onClick={()=>void save(v.variantId)}>保存卡面数量</button>
        {!!entry?.unassigned && <button className="secondary" disabled={busy} onClick={()=>void save(v.variantId,true)}>分配已有收藏</button>}
      </article>)}</div>
      {!data.variants.length && <p>上游未提供独立卡面，仍可在下方登记总量。</p>}
    </>}
  </section>;
}
