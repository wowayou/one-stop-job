import { Copy, Trash2 } from "lucide-react";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { OPPORTUNITY_DIMENSIONS } from "../lib/constants";
import { scoreClass } from "../lib/format";
import type { InterviewLog, Job } from "../types";
import { TextBlock } from "./TextBlock";

export function InterviewCard({
  log,
  job,
  busy,
  onDelete,
  onCopyMarkdown,
  showJob = false
}: {
  log: InterviewLog;
  job?: Job | null;
  busy: BusyState;
  onDelete: (log: InterviewLog) => Promise<void>;
  onCopyMarkdown: (log: InterviewLog) => Promise<void>;
  showJob?: boolean;
}) {
  const hasScores = Object.keys(log.score_details ?? {}).length > 0;
  return (
    <article className="interview-card">
      <div className="interview-card-head">
        <div className="interview-card-title">
          <strong>{log.round}</strong>
          {showJob && job && <span className="muted">{job.company_name} · {job.title}</span>}
          {log.interview_date && <span className="muted">{log.interview_date}</span>}
          {log.interviewer && <span className="muted">{log.interviewer}</span>}
        </div>
        <div className="interview-card-meta">
          {log.opportunity_score != null && <span className={scoreClass(log.opportunity_score)}>{log.opportunity_score}</span>}
          {log.conclusion && <span className="status">{log.conclusion}</span>}
          <button className="icon-button compact" title="复制复盘 Markdown" onClick={() => onCopyMarkdown(log)}>
            <Copy size={14} />
          </button>
          <button className="icon-button compact" title="删除复盘" onClick={() => onDelete(log)} disabled={hasBusy(busy, `interview-${log.id}`)}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      {hasScores && (
        <p className="interview-dims">
          {OPPORTUNITY_DIMENSIONS.map((dim) => `${dim.key} ${log.score_details[dim.key] ?? 0}/${dim.weight}`).join(" · ")}
        </p>
      )}
      {log.real_picture && <TextBlock title="岗位真实画像" text={log.real_picture} />}
      {log.qa_review && <TextBlock title="问题复盘" text={log.qa_review} />}
      {log.weaknesses && <TextBlock title="暴露短板" text={log.weaknesses} />}
      {log.next_actions && <TextBlock title="下一步动作" text={log.next_actions} />}
      {log.follow_up && <TextBlock title="跟进话术" text={log.follow_up} />}
    </article>
  );
}
