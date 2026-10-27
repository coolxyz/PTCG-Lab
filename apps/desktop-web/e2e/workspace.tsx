// Test-only shell: real product styles and pages, with network fixtures owned by Playwright.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { OnlineApp } from "../src/online/OnlineApp";
import { PracticeApp } from "../src/practice/PracticeApp";
import { Sync } from "../../web/src/Sync";
import "../../web/src/style.css";
function Workspace() {
  const [page, setPage] = useState("cards");
  return (
    <div className="app">
      <aside className="sidebar">
        <nav aria-label="测试页面">
          {[
            ["cards", "卡牌资料库"],
            ["online", "网络对战"],
            ["sync", "数据与对战更新"],
            ["practice", "人机练习"],
          ].map(([id, name]) => (
            <button
              key={id}
              className={page === id ? "active" : ""}
              onClick={() => setPage(id)}
            >
              {name}
            </button>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">工作空间</header>
        <main>
          {page === "cards" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">YOUR NEXT DECK STARTS HERE</div>
                  <h1>
                    每一张卡，皆有可能<span>。</span>
                  </h1>
                  <p>发现卡牌，整理收藏，构筑属于你的对战思路。</p>
                </div>
              </div>
              <div className="overview">卡牌资料库布局基线</div>
            </>
          )}
          {page === "online" && <OnlineApp product />}
          {page === "sync" && <Sync onPublished={() => {}} />}
          <PracticeApp
            active={page === "practice"}
            onBuild={() => setPage("cards")}
          />
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<Workspace />);
