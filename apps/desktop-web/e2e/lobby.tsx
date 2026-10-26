// Test-only entry: exercise the real product lobby against a routed bridge.
import { createRoot } from "react-dom/client";
import { OnlineApp } from "../src/online/OnlineApp";
createRoot(document.getElementById("root")!).render(<OnlineApp product />);
