import { useEffect, useState } from "react";
import { DemoApp } from "./DemoApp";
import { PracticeApp } from "./practice/PracticeApp";
declare const __PRACTICE_ENABLED__: boolean;
export function RootApp() {
  const [route, setRoute] = useState(() => window.location.hash);
  useEffect(() => {
    const changed = () => setRoute(window.location.hash);
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const practice = route === "#practice";
  return (
    <>
      <nav className="desktop-nav" aria-label="模式导航">
        <a href="#practice">本地人机练习</a>
        <a href="#demo">网络界面演示</a>
      </nav>
      {__PRACTICE_ENABLED__ && (
        <PracticeApp
          active={practice}
          onBuild={() => {
            window.location.hash = "#build";
          }}
        />
      )}
      {practice && !__PRACTICE_ENABLED__ && (
        <main className="demo-lobby">
          <h1>本地练习尚未启用</h1>
          <p>当前运行的是独立界面演示。</p>
        </main>
      )}
      {route === "#build" ? (
        <main className="demo-lobby">
          <h1>卡组构筑</h1>
          <p>
            当前尚未开放卡组构筑，请在练习大厅使用预设或已有卡组版本。
          </p>
          <a href="#practice">返回练习大厅</a>
        </main>
      ) : (
        !practice && <DemoApp />
      )}
    </>
  );
}
