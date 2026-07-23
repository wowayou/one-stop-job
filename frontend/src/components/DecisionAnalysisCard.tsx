import { CheckCircle2, Copy } from "lucide-react";
import { useState } from "react";
import { copyToClipboard } from "../api";
import { RUN_STATUS_BADGE } from "../lib/constants";
import type { DecisionAnalysis, RunStatus } from "../types";

export function DecisionAnalysisCard({ analysis, runStatus }: { analysis: DecisionAnalysis; runStatus: RunStatus }) {
  const checks = analysis.rule_checks ?? [];
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const badge = RUN_STATUS_BADGE[runStatus] ?? RUN_STATUS_BADGE.rules_only;

  async function copyReplyDraft() {
    const ok = await copyToClipboard(analysis.reply_draft);
    setCopyState(ok ? "copied" : "error");
    window.setTimeout(() => setCopyState("idle"), 1600);
  }

  return (
    <div className="decision-card">
      <div className="decision-head">
        <span className={`priority priority-${analysis.priority === "待确认" ? "unknown" : analysis.priority.toLowerCase()}`}>{analysis.priority}</span>
        <strong>{analysis.direction}</strong>
        <small className={`run-badge run-badge-${badge.tone}`} title={badge.title}>{badge.label}</small>
      </div>
      <div className="decision-next"><span>唯一下一步 · {analysis.next_action}</span><strong>{analysis.action_text}</strong></div>
      {!!checks.length && (
        <div className="decision-checks">
          {checks.map((check) => (
            <div className={`decision-check ${check.status}`} key={check.code} title={check.detail}>
              <span>{check.status === "pass" ? "✓" : check.status === "fail" ? "×" : "?"}</span>
              <div><strong>{check.label}</strong><small>{check.detail}</small></div>
            </div>
          ))}
        </div>
      )}
      {(analysis.reasons?.length > 0 || analysis.risks?.length > 0 || analysis.uncertainties?.length > 0) && (
        <div className="decision-columns">
          <div><span>判断依据</span><ul>{analysis.reasons?.map((item) => <li key={item}>{item}</li>)}</ul></div>
          <div><span>风险 / 待确认</span><ul>{[...(analysis.risks ?? []), ...(analysis.uncertainties ?? [])].map((item) => <li key={item}>{item}</li>)}</ul></div>
        </div>
      )}
      {analysis.reply_draft && (
        <div className="decision-draft">
          <span>可发送草稿</span><p>{analysis.reply_draft}</p>
          <button className={`small-action copy-feedback ${copyState}`} type="button" onClick={copyReplyDraft} aria-live="polite">
            {copyState === "copied" ? <CheckCircle2 size={14} /> : <Copy size={14} />}
            {copyState === "copied" ? "已复制" : copyState === "error" ? "复制失败" : "复制"}
          </button>
        </div>
      )}
      <p className="decision-boundary">{analysis.pipeline_recommendation?.reason || "当前为只读建议，不会自动执行。"}</p>
    </div>
  );
}
