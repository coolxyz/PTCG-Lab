export type ChineseDetails = {
  source?: string;
  revision?: number;
  textStatus?: string;
  sections: {
    kind: string;
    name: string;
    text: string;
    damage?: string;
    cost?: string[];
    locale?: string;
    versionLabel?: string;
    missingText?: boolean;
    source?: string;
  }[];
};

export function CardDescription({
  details,
  types,
}: {
  details?: ChineseDetails;
  types: Record<string, string>;
}) {
  return (
    <section className="chinese-description" aria-label="中文卡牌说明">
      <h3>中文卡牌说明</h3>
      {!details?.sections?.length && (
        <p className="muted">来源尚未提供可提取的中文效果说明。</p>
      )}
      {details?.sections?.map((s, i) => (
        <div className="card-rule-section" key={i}>
          <strong>
            {s.name === s.kind ? s.kind : `${s.kind} · ${s.name}`}
            {s.damage && `　${s.damage}`}
          </strong>
          {!!s.cost?.length && (
            <small>
              所需能量：{s.cost.map((t) => types[t] || t).join("、")}
            </small>
          )}
          {s.versionLabel && <small>{s.versionLabel}</small>}
          {s.text && <p>{s.text}</p>}
          {s.missingText && <p className="muted">此效果的中文说明待补。</p>}
          {s.locale === "zh-Hant" && <small>来源提供繁体中文说明</small>}
          {s.source && (
            <a href={s.source} target="_blank" rel="noreferrer">
              查看补充说明来源
            </a>
          )}
        </div>
      ))}
      {details?.source && (
        <a href={details.source} target="_blank" rel="noreferrer">
          查看百科原文{details.revision ? `（版本 ${details.revision}）` : ""}
        </a>
      )}
    </section>
  );
}
