import { useState } from "react";
import { createRoot } from "react-dom/client";
import { RetryImage } from "../../packages/battle-ui/src/RetryImage";
import type { ArtworkStatus } from "../../packages/battle-ui/src/artwork-retry";
function Fixture() {
  const [source, setSource] = useState(new URLSearchParams(location.search).get("case") || "transient");
  const [visible, setVisible] = useState(true);
  const [status, setStatus] = useState<ArtworkStatus>({state:"loading",message:""});
  return <><button onClick={() => setSource("replacement")}>切换图片</button><button onClick={() => setVisible(false)}>移除图片</button>
    {visible && <RetryImage src={`/api/sync/artwork/${source}`} alt="卡图 A" loading="lazy" onStatusChange={setStatus} />}
    <RetryImage src="/api/sync/artwork/good" alt="卡图 B" />
    <output aria-label="下载状态">{status.state}: {status.message}</output></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
