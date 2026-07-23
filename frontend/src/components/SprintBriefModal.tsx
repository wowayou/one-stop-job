import { CheckCircle2, ClipboardList, X } from "lucide-react";
import { useState } from "react";
import { copyToClipboard } from "../api";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { scoreClass } from "../lib/format";
import type { SprintBrief } from "../types";

export function SprintBriefModal({ brief, onClose }: { brief: SprintBrief; onClose: () => void }) {
  useEscapeClose(true, onClose);
  const [copied, setCopied] = useState(false);
  async function copyMarkdown() {
    const ok = await copyToClipboard(brief.markdown);
    setCopied(ok);
    if (ok) window.setTimeout(() => setCopied(false), 1200);
  }
  return (
    <div className="modal-backdrop">
      <div className="modal sprint-modal">
        <div className="modal-head">
          <div>
            <h2>今日求职冲刺包</h2>
            <p className="muted">
              Top {brief.top_jobs.length} 岗位，已准备 {brief.prepared.length} 个，新增 {brief.tasks_created.length} 个待办。
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="sprint-summary">
          {brief.top_jobs.slice(0, 5).map((job, index) => (
            <article key={job.id} className="sprint-job">
              <span>{index + 1}</span>
              <div>
                <strong>{job.company_name}</strong>
                <p>{job.title}</p>
              </div>
              <b className={scoreClass(job.latest_score.total)}>{job.latest_score.total}</b>
            </article>
          ))}
          {!brief.top_jobs.length && <p className="muted">暂无岗位。先采集或导入岗位后再生成冲刺包。</p>}
        </div>

        <label>
          可复制 Markdown
          <textarea className="sprint-markdown" readOnly value={brief.markdown} />
        </label>
        <div className="row-actions">
          <button type="button" className="primary-action" onClick={copyMarkdown}>
            {copied ? <CheckCircle2 size={18} /> : <ClipboardList size={18} />}
            {copied ? "已复制" : "复制 Markdown"}
          </button>
        </div>
      </div>
    </div>
  );
}
