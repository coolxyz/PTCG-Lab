import { useEffect, useMemo, useState } from "react";
import { OnlineApp } from "@ptcg/desktop-web/online";
import { errorText } from "../../desktop-web/src/online/controller";
import type { MobileOwner } from "./contracts";
import { nativeOnline, onlineAPI, mobileOnlineStorage } from "./online";
import "@ptcg/battle-ui/theme.css";
import "./network.css";

export function Network({ owner }: { owner: MobileOwner }) {
  const [origin, setOrigin] = useState(""),
    [ca, setCA] = useState(""),
    [configured, setConfigured] = useState(false),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [generation, setGeneration] = useState(0);
  const connect = useMemo(() => async () => onlineAPI(owner), [owner]);
  useEffect(() => {
    let active = true;
    void nativeOnline<{ origin: string; caPem: string } | null>(owner, {
      op: "configuration",
    })
      .then((value) => {
        if (active) {
          setOrigin(value?.origin || "");
          setCA(value?.caPem || "");
          setConfigured(!!value);
        }
      })
      .catch((e) => {
        if (active) setError(errorText(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [owner]);
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await nativeOnline(owner, {
        op: "configure",
        origin: origin.trim(),
        caPem: ca.trim(),
      });
      setConfigured(true);
      setGeneration((n) => n + 1);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mobile-network">
      <details
        className="mobile-panel"
        open={!configured}
        key={configured ? "configured" : "first"}
      >
        <summary>对战服务器{configured ? " · 已配置" : ""}</summary>
        <p>
          填写服务器提供的 HTTPS
          地址。只有你点击大厅中的建立身份按钮后才会创建游客。
        </p>
        <label>
          服务器地址
          <input
            type="url"
            autoCapitalize="none"
            autoCorrect="off"
            placeholder="https://battle.example.com"
            value={origin}
            onChange={(e) => setOrigin(e.target.value)}
          />
        </label>
        <details>
          <summary>局域网服务器的 CA 证书</summary>
          <p>
            使用公网可信证书时留空。局域网自签名环境请粘贴同批服务提供的公开 PEM
            CA；不要粘贴私钥。
          </p>
          <textarea
            aria-label="服务器公开 CA 证书"
            rows={6}
            value={ca}
            maxLength={32768}
            onChange={(e) => setCA(e.target.value)}
          />
        </details>
        <button
          className="primary"
          disabled={busy || loading || !origin.trim()}
          onClick={() => void save()}
        >
          保存服务器
        </button>
        <p>
          更换地址或 CA
          会选择独立身份配置，原身份及记录保留。存在待确认操作时请先恢复。
        </p>
      </details>
      {error && (
        <p role="alert" className="mobile-error">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">正在读取服务器设置…</p>
      ) : configured ? (
        <OnlineApp
          key={generation}
          product
          native
          connectAPI={connect}
          storage={mobileOnlineStorage}
        />
      ) : (
        <p className="mobile-empty">
          配置服务器后，即可使用已有卡组创建或加入房间。
        </p>
      )}
    </div>
  );
}
