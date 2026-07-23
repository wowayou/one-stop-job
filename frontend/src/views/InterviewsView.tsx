import { InterviewCard } from "../components/InterviewCard";
import type { BusyState } from "../hooks/useBusyState";
import type { InterviewLog, Job } from "../types";

export function InterviewsView({
  interviews,
  jobs,
  busy,
  onOpenJob,
  onDelete,
  onCopyMarkdown
}: {
  interviews: InterviewLog[];
  jobs: Job[];
  busy: BusyState;
  onOpenJob: (job: Job) => void;
  onDelete: (log: InterviewLog) => Promise<void>;
  onCopyMarkdown: (log: InterviewLog) => Promise<void>;
}) {
  return (
    <section className="content-panel interviews-panel">
      <div className="list-summary">
        <strong>面试复盘</strong>
      </div>
      {interviews.length ? (
        <div className="interview-list">
          {interviews.map((log) => {
            const job = jobs.find((item) => item.id === log.job_id);
            return (
              <div key={log.id} className="interview-list-item">
                <InterviewCard log={log} job={job} busy={busy} onDelete={onDelete} onCopyMarkdown={onCopyMarkdown} showJob />
                {job && (
                  <button className="small-action" onClick={() => onOpenJob(job)}>
                    打开岗位
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="muted">还没有面试复盘。面试结束后，在岗位抽屉的「面试复盘」里记录一次，这里会按时间线跨岗位汇总，方便追溯与迭代。</p>
      )}
    </section>
  );
}
