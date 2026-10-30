import React, { useState } from "react";
import type { CardInfo, Decision, EngineCard } from "./battle/types";
import { lookupCard } from "./battle/presentation";

function category(card: EngineCard) {
  return card.superType === "POKEMON"
    ? "宝可梦"
    : card.superType === "ENERGY"
      ? "能量"
      : "训练家";
}

/** Read-only view: it never owns or modifies the pending selection. */
export function DeckBrowser({
  inspection,
  names,
  selectedCount,
}: {
  inspection: NonNullable<Decision["deckInspection"]>;
  names: Record<string, CardInfo>;
  selectedCount: number;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("全部");
  const label = (card: EngineCard) =>
    lookupCard(card, names)?.cnName || card.name;
  const entries = [...inspection.cards].sort((a, b) =>
    label(a.card).localeCompare(label(b.card), "zh-CN"),
  );
  const visible = entries.filter(
    ({ card }) =>
      (filter === "全部" || category(card) === filter) &&
      `${label(card)} ${card.name} ${card.id || ""}`
        .toLowerCase()
        .includes(query.toLowerCase().trim()),
  );
  return (
    <details className="deck-browser">
      <summary>
        浏览{inspection.owner === "self" ? "我方" : "对方"}完整牌库 ·{" "}
        {inspection.total} 张
      </summary>
      <p className="deck-browser-note">
        仅供查看，按名称排列，不代表牌库顺序。下方已选 {selectedCount}{" "}
        张会保留；请在检索候选区选牌。
      </p>
      <label>
        查找卡牌
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="中文名、英文名或卡号"
        />
      </label>
      <div className="deck-browser-filters" role="group" aria-label="牌库分类">
        {["全部", "宝可梦", "训练家", "能量"].map((type) => (
          <button
            key={type}
            type="button"
            aria-pressed={filter === type}
            onClick={() => setFilter(type)}
          >
            {type}
          </button>
        ))}
      </div>
      <p role="status">
        显示 {visible.reduce((n, e) => n + e.count, 0)} / {inspection.total} 张
        · {visible.length} 种卡
      </p>
      <ul className="deck-browser-list" aria-label="完整牌库内容">
        {visible.map(({ card, count }) => {
          const info = lookupCard(card, names);
          return (
            <li key={card.id || card.name}>
              <details>
                <summary>
                  <strong>{label(card)}</strong>
                  <span>× {count}</span>
                </summary>
                <small>
                  {category(card)} · {card.id}
                </small>
                {info?.image?.url && (
                  <img
                    loading="lazy"
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                    }}
                    src={info.image.url}
                    alt={label(card)}
                  />
                )}
                {info?.chineseDetails?.sections?.length ? (
                  info.chineseDetails.sections.map((s, i) => (
                    <p key={i}>
                      <strong>{s.name}</strong> {s.damage} {s.text}
                    </p>
                  ))
                ) : (
                  <>
                    {card.abilities?.map((a, i) => (
                      <p key={`ability-${i}`}>
                        {a.name}：{a.text}
                      </p>
                    ))}
                    {card.attacks?.map((a, i) => (
                      <p key={`attack-${i}`}>
                        {a.name} {a.damage}：{a.text}
                      </p>
                    ))}
                  </>
                )}
              </details>
            </li>
          );
        })}
      </ul>
      {!visible.length && <p>没有符合筛选条件的卡牌。</p>}
    </details>
  );
}
