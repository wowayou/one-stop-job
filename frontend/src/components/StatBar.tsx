import type { FunnelAnalytics } from "../types";

export function StatBar({
  metrics,
  funnel,
  onShowJobs,
  onShowScoreQueue,
  onShowTasks,
  onShowPrep
}: {
  metrics: { total: number; fit: number; research: number; drafts: number };
  funnel: FunnelAnalytics | null;
  onShowJobs: (status?: string) => void;
  onShowScoreQueue: () => void;
  onShowTasks: () => void;
  onShowPrep: () => void;
}) {
  return (
    <section className="stat-bar" data-tour="metrics" aria-label="概览统计">
      <div className="stat-row">
        <button type="button" className="stat-chip" onClick={() => onShowJobs()} title="查看全部岗位">
          岗位 <b>{metrics.total}</b>
        </button>
        <button type="button" className="stat-chip" onClick={() => onShowJobs("fit")} title="查看合适岗位">
          合适 <b>{metrics.fit}</b>
        </button>
        <button type="button" className="stat-chip" onClick={() => onShowJobs("researching")} title="查看待调研岗位">
          待调研 <b>{metrics.research}</b>
        </button>
        <button type="button" className="stat-chip" onClick={onShowPrep} title="查看面试准备草稿">
          草稿 <b>{metrics.drafts}</b>
        </button>
      </div>
      {funnel && (
        <div className="stat-row">
          <span className="stat-label">现状</span>
          <span className="stat-chip">高分 <b>{funnel.summary.top_score_jobs}</b></span>
          <span className="stat-chip">已投 <b>{funnel.summary.applied_jobs}</b></span>
          <span className="stat-chip">面试 <b>{funnel.summary.interview_jobs}</b></span>
          <span className="stat-chip">Offer <b>{funnel.summary.offer_jobs}</b></span>
          <span className="stat-chip">待跟进 <b>{funnel.summary.stale_jobs}</b></span>
          <button type="button" className="small-action" onClick={onShowScoreQueue}>看高分队列</button>
          <button type="button" className="small-action" onClick={onShowTasks}>看待办</button>
        </div>
      )}
    </section>
  );
}
