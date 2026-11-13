import { useEffect, useState, type FormEvent } from "react";

type Configuration = {
  baseUrl: string;
  model: string;
  apiKey: string;
  responseFormat: "json_object" | "json_schema" | "text";
  reasoningEffort: string | null;
  timeoutSeconds: number;
  maxTokens: number;
  maxRetries: number;
};
type SavedSettings = {
  settings: Omit<Configuration, "apiKey"> | null;
  hasApiKey: boolean;
  environmentManaged: boolean;
};
type Probe = { ok: boolean; failure: string | null; milliseconds: number };

const defaults: Configuration = {
  baseUrl: "",
  model: "",
  apiKey: "",
  responseFormat: "json_object",
  reasoningEffort: null,
  timeoutSeconds: 20,
  maxTokens: 4096,
  maxRetries: 2,
};

async function request<T>(path: string, values?: Configuration): Promise<T> {
  const response = await fetch(
    "/api/battle/llm/" + path,
    values
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        }
      : undefined,
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "LLM 设置请求失败。");
  return data;
}

function failureMessage(code: string | null) {
  switch (code) {
    case "connection":
      return "无法连接 API 服务。请检查服务地址及网络。";
    case "timeout":
      return "API 请求超时。";
    case "http-401":
    case "http-403":
      return "API 拒绝授权。请检查密钥和模型权限。";
    case "http-404":
      return "API 未找到请求路径或模型。请检查服务地址和模型名称。";
    case "invalid-response":
      return "模型未返回可执行的选择。请检查响应格式和参数。";
    default:
      return `API 请求失败（${code ?? "未知错误"}）。`;
  }
}

export function LLMSettings({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [values, setValues] = useState(defaults);
  const [saved, setSaved] = useState<SavedSettings>();
  const [busy, setBusy] = useState<"load" | "save" | "test" | null>("load");
  const [notice, setNotice] = useState<{ text: string; error?: boolean }>();
  useEffect(() => {
    let active = true;
    request<SavedSettings>("settings")
      .then((result) => {
        if (!active) return;
        setSaved(result);
        setValues({ ...defaults, ...result.settings });
      })
      .catch(() => {
        if (active) setNotice({ text: "无法读取本机 LLM 配置。", error: true });
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    return () => {
      active = false;
    };
  }, []);

  function change<K extends keyof Configuration>(
    key: K,
    value: Configuration[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    setNotice(undefined);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const action =
      (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") ===
      "test"
        ? "test"
        : "save";
    setBusy(action);
    setNotice(undefined);
    try {
      if (action === "test") {
        const result = await request<Probe>("test", values);
        setNotice(
          result.ok
            ? {
                text: `连接成功 · ${(result.milliseconds / 1000).toFixed(2)} 秒`,
              }
            : { text: failureMessage(result.failure), error: true },
        );
      } else {
        const result = await request<SavedSettings>("settings", values);
        setSaved(result);
        setValues({ ...defaults, ...result.settings });
        setNotice({ text: "已保存。新开对局时可选择此模型。" });
        onSaved();
      }
    } catch (error) {
      setNotice({
        text: error instanceof Error ? error.message : "LLM 设置请求失败。",
        error: true,
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section
      className="practice-panel llm-settings"
      aria-labelledby="llm-settings-heading"
    >
      <header className="llm-settings-header">
        <div>
          <h2 id="llm-settings-heading">LLM 对手设置</h2>
          <p>使用兼容 Chat Completions 的 API。连接测试会发送一条合成请求。</p>
        </div>
        <button
          className="workspace-button is-secondary"
          onClick={onClose}
          disabled={!!busy}
        >
          关闭
        </button>
      </header>
      {saved?.environmentManaged && (
        <p className="practice-notice">当前配置由启动环境变量管理。</p>
      )}
      <form onSubmit={submit}>
        <fieldset disabled={!!busy || !saved || saved.environmentManaged}>
          <div className="llm-settings-grid">
            <label>
              API 地址
              <input
                type="url"
                placeholder="https://api.openai.com/v1"
                value={values.baseUrl}
                onChange={(e) => change("baseUrl", e.target.value)}
                required
              />
            </label>
            <label>
              模型名称
              <input
                placeholder="输入服务商提供的模型 ID"
                value={values.model}
                maxLength={160}
                onChange={(e) => change("model", e.target.value)}
                required
              />
            </label>
            <label>
              API 密钥
              <input
                type="password"
                autoComplete="new-password"
                value={values.apiKey}
                placeholder={
                  saved?.hasApiKey ? "已保存，留空继续使用" : "输入 API 密钥"
                }
                onChange={(e) => change("apiKey", e.target.value)}
              />
            </label>
            <label>
              响应格式
              <select
                value={values.responseFormat}
                onChange={(e) =>
                  change(
                    "responseFormat",
                    e.target.value as Configuration["responseFormat"],
                  )
                }
              >
                <option value="json_object">JSON 对象</option>
                <option value="json_schema">JSON Schema</option>
                <option value="text">文本</option>
              </select>
            </label>
          </div>
          <details>
            <summary>高级选项</summary>
            <div className="llm-settings-grid">
              <label>
                推理强度
                <select
                  value={values.reasoningEffort ?? ""}
                  onChange={(e) =>
                    change("reasoningEffort", e.target.value || null)
                  }
                >
                  <option value="">服务商默认</option>
                  {[
                    "none",
                    "minimal",
                    "low",
                    "medium",
                    "high",
                    "xhigh",
                    "max",
                  ].map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                超时（秒）
                <input
                  type="number"
                  min={1}
                  max={60}
                  required
                  value={values.timeoutSeconds}
                  onChange={(e) =>
                    change("timeoutSeconds", Number(e.target.value))
                  }
                />
              </label>
              <label>
                最大输出 token
                <input
                  type="number"
                  min={128}
                  max={32768}
                  required
                  value={values.maxTokens}
                  onChange={(e) => change("maxTokens", Number(e.target.value))}
                />
              </label>
              <label>
                最大重试次数
                <input
                  type="number"
                  min={0}
                  max={4}
                  required
                  value={values.maxRetries}
                  onChange={(e) => change("maxRetries", Number(e.target.value))}
                />
              </label>
            </div>
          </details>
        </fieldset>
        <div className="llm-settings-actions">
          <button
            className="workspace-button is-secondary"
            type="submit"
            value="test"
            disabled={!!busy || !saved}
          >
            {busy === "test" ? "正在测试…" : "测试连接"}
          </button>
          <button
            className="workspace-button"
            type="submit"
            value="save"
            disabled={!!busy || !saved || saved.environmentManaged}
          >
            {busy === "save" ? "正在保存…" : "保存设置"}
          </button>
        </div>
      </form>
      {notice && (
        <p
          className={`practice-notice${notice.error ? " is-error" : ""}`}
          role={notice.error ? "alert" : "status"}
        >
          {notice.text}
        </p>
      )}
    </section>
  );
}
