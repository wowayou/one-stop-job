import { CalendarCheck, ChevronLeft, ChevronRight, FileQuestion, Gauge, NotebookPen, Pencil, Pin, Send, Trash2, X } from "lucide-react";
import { FormEvent, RefObject } from "react";
import { InterviewCard } from "../components/InterviewCard";
import { InterviewForm } from "../components/InterviewForm";
import { JobEventsSection } from "../components/JobEventsSection";
import { ScoreBreakdown } from "../components/ScoreBreakdown";
import { TextBlock } from "../components/TextBlock";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { jobStatuses, statusLabels } from "../lib/constants";
import { scoreClass } from "../lib/format";
import type { ApplicationEvent, Company, FitScore, InterviewLog, InterviewPrep, Job, ResearchItem } from "../types";

const sourceTypes = ["company_site", "job_post", "search", "xhs", "maimai", "kanzhun", "manual_note"];

const recruitmentStatusLabels: Record<string, string> = {
  active: "在招",
  closed: "已关闭",
  unknown: "未知"
};

export function JobDrawer({
  job,
  company,
  research,
  scores,
  prep,
  events,
  drawerRef,
  researchForm,
  busy,
  onClose,
  onEdit,
  onPatch,
  onResearchForm,
  onAddResearch,
  onScore,
  onPrep,
  aiAvailable,
  useAiPrep,
  onUseAiPrepChange,
  onTask,
  onAddEvent,
  onDeleteEvent,
  interviews,
  onAddInterview,
  onDeleteInterview,
  onCopyInterviewMarkdown,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  position,
  onDelete
}: {
  job: Job;
  company: Company | null;
  research: ResearchItem[];
  scores: FitScore[];
  prep: InterviewPrep | null;
  events: ApplicationEvent[];
  drawerRef: RefObject<HTMLElement | null>;
  researchForm: {
    source_type: string;
    title: string;
    summary: string;
    source_url: string;
    sentiment: string;
    confidence: number;
  };
  busy: BusyState;
  onClose: () => void;
  onEdit: () => void;
  onPatch: (job: Job, updates: Partial<Job>) => Promise<void>;
  onResearchForm: (value: typeof researchForm) => void;
  onAddResearch: (event: FormEvent) => Promise<void>;
  onScore: () => Promise<void>;
  onPrep: () => Promise<void>;
  aiAvailable: boolean;
  useAiPrep: boolean;
  onUseAiPrepChange: (value: boolean) => void;
  onTask: () => Promise<void>;
  onAddEvent: (payload: { event_type: string; event_date: string; channel?: string; note?: string }) => Promise<void>;
  onDeleteEvent: (event: ApplicationEvent) => Promise<void>;
  interviews: InterviewLog[];
  onAddInterview: (payload: Partial<InterviewLog>) => Promise<void>;
  onDeleteInterview: (log: InterviewLog) => Promise<void>;
  onCopyInterviewMarkdown: (log: InterviewLog) => Promise<void>;
  onPrev: () => void;
  onNext: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  position: string;
  onDelete?: (job: Job) => void;
}) {
  useEscapeClose(true, onClose);
  const latestScore = scores[0] ?? job.latest_score;
  return (
    <aside className="drawer" ref={drawerRef}>
      <div className="drawer-head">
        <div>
          <h2>{job.title}</h2>
          <p>{job.company_name}</p>
        </div>
        <div className="drawer-nav">
          <button className="icon-button" onClick={onPrev} disabled={!hasPrev} title="上一个岗位">
            <ChevronLeft size={18} />
          </button>
          {position && <span className="drawer-nav-pos">{position}</span>}
          <button className="icon-button" onClick={onNext} disabled={!hasNext} title="下一个岗位">
            <ChevronRight size={18} />
          </button>
          <button className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
      </div>
      <div className="drawer-actions">
        <button className="icon-text" onClick={() => onPatch(job, { favorite: !job.favorite })}>
          <Pin size={16} />
          {job.favorite ? "取消置顶" : "置顶"}
        </button>
        <button className="icon-text" onClick={onEdit}>
          <Pencil size={16} />
          编辑
        </button>
        <button className="icon-text" onClick={onTask} disabled={hasBusy(busy, "task")}>
          <CalendarCheck size={16} />
          加待办
        </button>
        {job.url && (
          <a className="icon-text" href={job.url} target="_blank" rel="noreferrer">
            <Send size={16} />
            原链接
          </a>
        )}
        {onDelete && (
          <button className="icon-text danger-text" onClick={() => onDelete(job)} title="移入回收站">
            <Trash2 size={16} />
            删除
          </button>
        )}
      </div>

      <section className="drawer-section">
        <h3>求职状态</h3>
        <div className="status-grid">
          {jobStatuses.map((item) => (
            <button
              key={item}
              className={job.status === item ? "status-choice active" : "status-choice"}
              onClick={() => onPatch(job, { status: item })}
              disabled={job.status === item}
            >
              {statusLabels[item]}
            </button>
          ))}
        </div>
      </section>

      <section className="drawer-section">
        <h3>岗位快照</h3>
        <dl className="detail-grid">
          <div>
            <dt>薪资</dt>
            <dd>{job.salary_text || "-"}</dd>
          </div>
          <div>
            <dt>地点</dt>
            <dd>{[job.city, job.area].filter(Boolean).join(" · ") || "-"}</dd>
          </div>
          <div>
            <dt>经验</dt>
            <dd>{job.experience || "-"}</dd>
          </div>
          <div>
            <dt>学历</dt>
            <dd>{job.degree || "-"}</dd>
          </div>
          <div>
            <dt>发布时间</dt>
            <dd>{job.published_at || "-"}</dd>
          </div>
          <div>
            <dt>招聘状态</dt>
            <dd>{recruitmentStatusLabels[job.recruitment_status] ?? job.recruitment_status ?? "-"}</dd>
          </div>
        </dl>
        <p className="long-text">{job.description || job.skills || "暂无 JD 详情"}</p>
      </section>

      <section className="drawer-section">
        <div className="section-title">
          <Gauge size={18} />
          <h3>匹配评分</h3>
          <button className="small-action" onClick={onScore} disabled={hasBusy(busy, "score")}>
            重新评分
          </button>
        </div>
        {latestScore ? (
          <div className="score-detail">
            <span className={scoreClass(latestScore.total)}>{latestScore.total}</span>
            <ScoreBreakdown score={latestScore} />
          </div>
        ) : (
          <p className="muted">尚未评分。点右上角「重新评分」即可按当前权重计算。</p>
        )}
      </section>

      <section className="drawer-section">
        <h3>公司证据</h3>
        <div className="evidence-list">
          {research.map((item) => (
            <article key={item.id} className="evidence-item">
              <div>
                <strong>{item.title}</strong>
                <span>
                  {item.source_type} · {item.sentiment} · {Math.round(item.confidence * 100)}%
                </span>
              </div>
              <p>{item.summary}</p>
              {item.source_url && (
                <a href={item.source_url} target="_blank" rel="noreferrer">
                  来源
                </a>
              )}
            </article>
          ))}
          {!research.length && <p className="muted">{company?.name ?? job.company_name} 暂无调研证据</p>}
        </div>
        <form className="research-form" onSubmit={onAddResearch}>
          <select value={researchForm.source_type} onChange={(event) => onResearchForm({ ...researchForm, source_type: event.target.value })}>
            {sourceTypes.map((type) => (
              <option key={type}>{type}</option>
            ))}
          </select>
          <select value={researchForm.sentiment} onChange={(event) => onResearchForm({ ...researchForm, sentiment: event.target.value })}>
            <option value="neutral">neutral</option>
            <option value="positive">positive</option>
            <option value="negative">negative</option>
          </select>
          <input value={researchForm.title} onChange={(event) => onResearchForm({ ...researchForm, title: event.target.value })} placeholder="证据标题" required />
          <input value={researchForm.source_url} onChange={(event) => onResearchForm({ ...researchForm, source_url: event.target.value })} placeholder="URL" />
          <textarea value={researchForm.summary} onChange={(event) => onResearchForm({ ...researchForm, summary: event.target.value })} placeholder="证据摘要" required />
          <button className="primary-action" disabled={hasBusy(busy, "research")}>
            保存证据
          </button>
        </form>
      </section>

      <section className="drawer-section">
        <div className="section-title">
          <FileQuestion size={18} />
          <h3>面试准备</h3>
          {aiAvailable && (
            <label className="ai-tailor-toggle" title="开启后按该岗位 JD 和你的画像用 AI 定制；关闭则用静态模板">
              <input type="checkbox" checked={useAiPrep} onChange={(event) => onUseAiPrepChange(event.target.checked)} />
              AI 定制
            </label>
          )}
          <div className="section-head-actions">
            <button className="small-action" onClick={onPrep} disabled={hasBusy(busy, "prep")}>
              生成
            </button>
          </div>
        </div>
        {aiAvailable && (
          <p className="muted prep-mode-hint">
            {useAiPrep ? "将按 JD + 画像用 AI 定制（不可用或失败时回退模板）" : "将使用静态模板生成"}
          </p>
        )}
        {prep ? (
          <div className="prep-block">
            <TextBlock title="JD 摘要" text={prep.jd_summary} />
            <TextBlock title="技能差距" text={prep.skill_gaps} />
            <TextBlock title="核心优势话术" text={prep.core_pitch} />
            <TextBlock title="沟通草稿" text={prep.communication_draft} />
            <TextBlock title="简历强调点" text={prep.resume_points} />
            <TextBlock title="对应简历" text={prep.tailored_resume} />
            <TextBlock title="STAR 素材" text={prep.star_stories} />
            <TextBlock title="反问问题" text={prep.questions_to_ask} />
          </div>
        ) : (
          <p className="muted">尚未生成准备包</p>
        )}
      </section>

      <section className="drawer-section">
        <div className="section-title">
          <Send size={18} />
          <h3>投递事件</h3>
          <small className="muted">{events.length ? `${events.length} 条` : "记录真实动作"}</small>
        </div>
        <JobEventsSection events={events} busy={busy} onAddEvent={onAddEvent} onDeleteEvent={onDeleteEvent} />
      </section>

      <section className="drawer-section">
        <div className="section-title">
          <NotebookPen size={18} />
          <h3>面试复盘</h3>
          <small className="muted">{interviews.length ? `${interviews.length} 轮` : "面试后记录"}</small>
        </div>
        <div className="interview-list">
          {interviews.map((log) => (
            <InterviewCard key={log.id} log={log} job={job} busy={busy} onDelete={onDeleteInterview} onCopyMarkdown={onCopyInterviewMarkdown} />
          ))}
          {!interviews.length && <p className="muted">面试结束后在这里记录：机会评分、问题复盘、暴露的短板和下一步动作，按轮次累积成可追溯的闭环。</p>}
        </div>
        <InterviewForm key={job.id} busy={busy} onAdd={onAddInterview} />
      </section>
    </aside>
  );
}
