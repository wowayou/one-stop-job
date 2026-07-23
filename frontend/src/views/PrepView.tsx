import { FileQuestion, MessageSquareText } from "lucide-react";
import { draftKindLabel } from "../lib/format";
import type { Draft, Job } from "../types";

export function PrepView({
  jobs,
  drafts,
  onOpen
}: {
  jobs: Job[];
  drafts: Draft[];
  onOpen: (job: Job) => void;
}) {
  const preppedJobIds = new Set(drafts.map((draft) => draft.job_id).filter(Boolean));
  return (
    <section className="split-view">
      <div className="content-panel queue-panel">
        <div className="section-title">
          <FileQuestion size={18} />
          <h2>准备队列</h2>
        </div>
        <div className="rank-list scroll-list">
          {jobs
            .filter((job) => job.status !== "rejected")
            .map((job) => (
              <button key={job.id} className="rank-row" onClick={() => onOpen(job)}>
                <span className={preppedJobIds.has(job.id) ? "pill good" : "pill"}>{preppedJobIds.has(job.id) ? "已生成" : "待生成"}</span>
                <strong>{job.title}</strong>
                <small>{job.company_name}</small>
              </button>
            ))}
        </div>
      </div>
      <div className="content-panel queue-panel">
        <div className="section-title">
          <MessageSquareText size={18} />
          <h2>准备素材</h2>
        </div>
        <div className="draft-list scroll-list">
          {drafts.map((draft) => (
            <article key={draft.id} className="draft-item">
              <span>{draftKindLabel(draft.kind)}</span>
              <p>{draft.content}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
