import { createRoot } from "react-dom/client";
import { App } from "./App";
import { nativeOwner, message } from "./contracts";

const root = createRoot(document.getElementById("root")!);
try {
  root.render(<App owner={nativeOwner()} />);
} catch (error) {
  root.render(
    <main className="mobile-start-error">
      <h1>欢迎来到 PTCG Lab</h1>
      <p>{message(error)}</p>
      <p>已保存的收藏和卡组会保留在本机。</p>
    </main>,
  );
}
