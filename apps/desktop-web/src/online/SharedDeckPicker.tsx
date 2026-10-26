import { useEffect, useState } from "react";
import { OnlineController, errorText, type ProductDeck } from "./controller";
import { preferredRevision } from "./storage";

export function SharedDeckPicker({
  controller: c,
  disabled,
}: {
  controller: OnlineController;
  disabled: boolean;
}) {
  const [decks, setDecks] = useState<ProductDeck[]>([]);
  const [selected, setSelected] = useState("");
  const [revision, setRevision] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [check, setCheck] = useState<Awaited<
    ReturnType<OnlineController["checkRevision"]>
  > | null>(null);
  const applyRows = (rows: ProductDeck[], preferred = revision) => {
    setDecks(rows);
    const chosen =
      rows.find((d) => d.revisions.some((r) => r.id === preferred)) ||
      rows.find((d) => d.id === selected);
    setSelected(chosen?.id || "");
    setRevision(
      chosen?.revisions.some((r) => r.id === preferred) ? preferred : "",
    );
    setLoaded(true);
  };
  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    void c
      .productDecks()
      .then(({ decks: rows }) => {
        if (!cancelled)
          applyRows(
            rows,
            c.snapshot().remembered.localRevisionId || preferredRevision(),
          );
      })
      .catch((e) => {
        if (!cancelled) setError(errorText(e));
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [c]);
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      applyRows((await c.productDecks()).decks);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let cancelled = false;
    setCheck(null);
    setError("");
    if (revision)
      void c
        .checkRevision(revision)
        .then((value) => {
          if (!cancelled) setCheck(value);
        })
        .catch((e) => {
          if (!cancelled) setError(errorText(e));
        });
    return () => {
      cancelled = true;
    };
  }, [c, revision]);
  const deck = decks.find((d) => d.id === selected);
  const chosen = deck?.revisions.find((r) => r.id === revision);
  const activeRevision = c.snapshot().remembered.revision
    ? c.snapshot().remembered.localRevisionId
    : undefined;
  const activeDeck = decks.find((d) =>
    d.revisions.some((r) => r.id === activeRevision),
  );
  const activeNumber = activeDeck?.revisions.find(
    (r) => r.id === activeRevision,
  )?.number;
  return (
    <div className="lobby-deck-picker">
      <label>
        平台已有卡组{" "}
        <span className="online-muted">
          {loaded ? `${decks.length} 套` : "正在加载…"}
        </span>
        <select
          aria-label="平台已有卡组"
          value={selected}
          disabled={disabled || busy}
          onChange={(e) => {
            const next = decks.find((d) => d.id === e.target.value);
            setSelected(e.target.value);
            setRevision(
              next?.revisions.reduce<
                ProductDeck["revisions"][number] | undefined
              >(
                (latest, r) =>
                  !latest || r.number > latest.number ? r : latest,
                undefined,
              )?.id || "",
            );
          }}
        >
          <option value="">请选择已保存的卡组</option>
          {decks.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </label>
      {loaded && !decks.length && (
        <p className="lobby-hint">
          还没有保存的卡组，先前往卡组构筑创建一套吧。
        </p>
      )}
      <div className="lobby-deck-preview">
        <div className="lobby-deck-mark" aria-hidden="true">
          {deck?.name.slice(0, 1) || "?"}
        </div>
        <div>
          <strong>{deck?.name || "选择你的出战卡组"}</strong>
          <p>
            {chosen
              ? `版本 ${chosen.number} · ${chosen.count} 张`
              : deck
                ? "保存的卡表需要先冻结为对战版本"
                : "与人机练习共用平台卡组"}
          </p>
          <span className="lobby-badge">
            {revision && revision === activeRevision
              ? "正在使用此版本"
              : "尚未提交此选择"}
          </span>
        </div>
      </div>
      <label>
        冻结版本
        <select
          aria-label="冻结版本"
          value={revision}
          disabled={disabled || busy || !deck}
          onChange={(e) => setRevision(e.target.value)}
        >
          <option value="">请选择不可变版本</option>
          {deck?.revisions.map((r) => (
            <option key={r.id} value={r.id}>
              版本 {r.number} · {r.count} 张
            </option>
          ))}
        </select>
      </label>
      <div className="lobby-deck-check" role="status">
        {revision ? (
          check ? (
            <>
              <span>
                本地检查：{check.total} 张 ·{" "}
                {check.issues.length
                  ? `${check.issues.length} 项提示`
                  : "未发现问题"}
              </span>
              {!!check.issues.length && (
                <ul>
                  {check.issues.map((issue, i) => (
                    <li key={i}>
                      {issue.explanation}：{issue.cardRefs.join("、")}{" "}
                      {issue.suggestedFix}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            "正在检查所选版本…"
          )
        ) : (
          "选择版本后检查卡组，以服务器准入结果为准。"
        )}
      </div>
      <button
        className="lobby-wide"
        disabled={disabled || busy || !chosen}
        onClick={() => void c.useRevision(revision)}
      >
        校验并使用此版本 <span aria-hidden="true">→</span>
      </button>
      <div className="lobby-actions">
        <button
          className="lobby-secondary"
          disabled={disabled || busy || !deck}
          onClick={async () => {
            if (!deck) return;
            setBusy(true);
            setError("");
            try {
              await c.freezeDeck(deck.id, deck.version);
              const rows = (await c.productDecks()).decks;
              const fresh = rows
                .find((d) => d.id === deck.id)
                ?.revisions.reduce<
                  ProductDeck["revisions"][number] | undefined
                >((latest, r) => (!latest || r.number > latest.number ? r : latest), undefined);
              applyRows(rows, fresh?.id || "");
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          冻结已保存卡表
        </button>
        <button
          className="lobby-text-button"
          disabled={disabled || busy}
          onClick={() => void refresh()}
        >
          刷新卡组列表
        </button>
      </div>
      {activeRevision && (
        <p className="lobby-submitted" role="status">
          当前已提交：
          {activeDeck
            ? `${activeDeck.name} · 版本 ${activeNumber}`
            : "之前提交的冻结版本（本地已删除或未列出）"}
          。更换选择后需点击“校验并使用此版本”才会替换。
        </p>
      )}
      <p className="online-muted">
        草稿需先保存，再冻结为对战版本。编辑卡组不会改变已提交的版本。
        <a href="/#decks">前往卡组构筑 ↗</a>
      </p>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
