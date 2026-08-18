import { Plus, X } from "lucide-react";
import { useState } from "react";
import { api, errorMessage, jsonBody } from "../api";
import type { UserProfile } from "../types";

/** 排除项标签式编辑器：每个排除词一个可删除标签 + 底部输入框加词。
 *
 * 直接调 /api/profile/dealbreakers 增删，不依赖外层画像表单提交——看到不想要的岗位
 * 加一个词、想松绑删一个词，立即生效，不用「填完整个表单再点保存」。
 * 改动后同步刷新本地 profile 状态，避免外层表单提交时用旧值覆盖。
 */
export function DealbreakerChips({
  profile,
  onUpdated,
  onNotify,
}: {
  profile: UserProfile;
  onUpdated: (profile: UserProfile) => void;
  onNotify: (kind: "success" | "error", message: string) => void;
}) {
  const words = (profile.dealbreakers || "")
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function addWord() {
    const word = input.trim();
    if (!word || busy) return;
    setBusy(true);
    try {
      const reply = await api<{ dealbreakers: string[] }>("/api/profile/dealbreakers", {
        method: "POST",
        ...jsonBody({ word }),
      });
      onUpdated({ ...profile, dealbreakers: reply.dealbreakers.join(",") });
      setInput("");
    } catch (err) {
      onNotify("error", errorMessage(err, "添加排除词失败"));
    } finally {
      setBusy(false);
    }
  }

  async function removeWord(word: string) {
    if (busy) return;
    setBusy(true);
    try {
      const reply = await api<{ dealbreakers: string[] }>(
        `/api/profile/dealbreakers?word=${encodeURIComponent(word)}`,
        { method: "DELETE" }
      );
      onUpdated({ ...profile, dealbreakers: reply.dealbreakers.join(",") });
    } catch (err) {
      onNotify("error", errorMessage(err, "删除排除词失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dealbreaker-chips">
      <div className="chip-list">
        {words.map((word) => (
          <span key={word} className="chip">
            {word}
            <button
              type="button"
              className="chip-remove"
              aria-label={`删除排除词 ${word}`}
              disabled={busy}
              onClick={() => void removeWord(word)}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {words.length === 0 && <small className="muted">尚未配置排除词</small>}
      </div>
      <div className="chip-input-row">
        <input
          type="text"
          value={input}
          placeholder="加一个排除词，如「主管」「销售」"
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void addWord();
            }
          }}
        />
        <button type="button" className="small-action" disabled={busy || !input.trim()} onClick={() => void addWord()}>
          <Plus size={14} />
          添加
        </button>
      </div>
    </div>
  );
}
