import { useEffect, useState } from "react";

type Props = { coins: ("heads" | "tails")[]; animate: boolean; onPlaying?: (playing: boolean) => void };
export function CoinResults({ coins, animate, onPlaying }: Props) {
  const [index, setIndex] = useState(0);
  const [finished, setFinished] = useState(false);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setReduced(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  const playing = coins.length > 0 && animate && !reduced && !finished;
  useEffect(() => { onPlaying?.(playing); return () => onPlaying?.(false); }, [playing, onPlaying]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setTimeout(() => {
      if (index + 1 < coins.length) setIndex(i => i + 1);
      else setFinished(true);
    }, 1300);
    return () => window.clearTimeout(timer);
  }, [index, playing, coins.length]);
  if (!coins.length) return null;
  const label = (coin: string) => coin === "heads" ? "正面" : "反面";
  return <section className="coin-results" aria-label="硬币投掷结果" aria-live="polite">
    <div className={`result-coin ${playing ? "flipping" : ""}`} key={index} data-result={coins[index]} aria-hidden="true">{label(coins[index])}</div>
    <div><strong>投掷硬币{playing ? ` · 第 ${index + 1} / ${coins.length} 次` : ` · 共 ${coins.length} 次`}</strong>
      <div className="coin-history">{coins.slice(0, playing ? index + 1 : coins.length).map((coin, i) => <span key={i}>{i + 1}. {label(coin)}</span>)}</div>
    </div>
    {playing && <button onClick={() => setFinished(true)}>显示全部结果</button>}
  </section>;
}
