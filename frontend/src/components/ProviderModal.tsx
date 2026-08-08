import { CheckCircle2, X } from "lucide-react";
import { FormEvent, useState } from "react";
import { useEscapeClose } from "../hooks/useEscapeClose";

export type ProviderModalSaveValues = {
  label: string;
  baseUrl: string;
  model: string;
  key: string;
  applyKeyTo: number[];
};

// 「+ 添加 Provider」和卡片「编辑」共用同一个弹窗；区别只在 mode 和初始值。
// Key 输入框全程不预填已保存的值（红线：不回显）——编辑时留空按钮下方会提示「已配置，留空不改」。
export function ProviderModal({
  mode,
  initialLabel,
  initialBaseUrl,
  initialModel,
  hasKey,
  otherProviders,
  saving,
  onClose,
  onSave
}: {
  mode: "add" | "edit";
  initialLabel: string;
  initialBaseUrl: string;
  initialModel: string;
  hasKey: boolean;
  otherProviders: { index: number; label: string }[];
  saving: boolean;
  onClose: () => void;
  onSave: (values: ProviderModalSaveValues) => Promise<void>;
}) {
  useEscapeClose(true, onClose);
  const [label, setLabel] = useState(initialLabel);
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [model, setModel] = useState(initialModel);
  const [key, setKey] = useState("");
  const [applyKeyTo, setApplyKeyTo] = useState<number[]>([]);

  function toggleApplyTarget(index: number) {
    setApplyKeyTo((current) => (current.includes(index) ? current.filter((i) => i !== index) : [...current, index]));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSave({ label: label.trim(), baseUrl: baseUrl.trim(), model: model.trim(), key: key.trim(), applyKeyTo });
  }

  return (
    <div className="modal-backdrop">
      <form className="modal provider-modal" onSubmit={submit}>
        <div className="modal-head">
          <h2>{mode === "add" ? "添加 Provider" : "编辑 Provider"}</h2>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="form-grid">
          <label>
            名称
            <input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="如「阿里 Qwen 视觉」（可选，仅本页展示用）"
            />
          </label>
          <label>
            Base URL
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
            />
          </label>
          <label>
            Model
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="qwen-vl-max" />
          </label>
        </div>
        <label>
          API Key
          <input
            type="password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder={hasKey ? "已配置（留空不改）" : mode === "add" ? "sk-...（可留空，稍后再补）" : "sk-..."}
            autoComplete="new-password"
          />
        </label>
        <p className="muted provider-modal-hint">
          Key 只写本机 <code>.env</code>，不回显；编辑时留空 = 不改 Key。
        </p>

        {otherProviders.length > 0 && (
          <details className="provider-key-share">
            <summary>这次填写的 Key 也同时写入其它 Provider…</summary>
            <div className="provider-key-share-options">
              {otherProviders.map(({ index, label: otherLabel }) => (
                <label className="provider-key-share-option" key={index}>
                  <input
                    type="checkbox"
                    checked={applyKeyTo.includes(index)}
                    onChange={() => toggleApplyTarget(index)}
                  />
                  <span>{otherLabel}</span>
                </label>
              ))}
            </div>
          </details>
        )}

        <button className="primary-action" disabled={saving}>
          <CheckCircle2 size={18} />
          {saving ? "保存中…" : "保存"}
        </button>
      </form>
    </div>
  );
}
