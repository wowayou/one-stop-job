import { AlertTriangle, CheckCircle2, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { sortedTasks } from "../lib/format";
import type { FollowUpTask, Job, StaleJob } from "../types";

const taskStatusLabels: Record<string, string> = {
  todo: "待办",
  done: "完成"
};

export function TasksView({
  tasks,
  staleJobs,
  jobs,
  busy,
  onAddTask,
  onUpdateTask,
  onDeleteTask,
  onOpenJob
}: {
  tasks: FollowUpTask[];
  staleJobs: StaleJob[];
  jobs: Job[];
  busy: BusyState;
  onAddTask: (title: string, jobId?: number, dueDate?: string) => Promise<void>;
  onUpdateTask: (task: FollowUpTask, updates: Partial<FollowUpTask>) => Promise<void>;
  onDeleteTask: (task: FollowUpTask) => Promise<void>;
  onOpenJob: (job: Job) => void;
}) {
  const [title, setTitle] = useState("");
  const [jobId, setJobId] = useState("");
  const [dueDate, setDueDate] = useState("");
  const taskBusy = Object.keys(busy).some((key) => key.startsWith("task"));
  return (
    <section className="content-panel tasks-panel">
      <div className="tasks-top">
      <div className="list-summary">
        <strong>待办清单</strong>
      </div>
      {staleJobs.length > 0 && (
        <div className="stale-callout">
          <div className="stale-callout-head">
            <AlertTriangle size={16} />
            <strong>需跟进（{staleJobs.length}）</strong>
            <span className="muted">fit/interview 久无进展，记得主动联系或更新状态</span>
          </div>
          <ul className="stale-list">
            {staleJobs.map((item) => {
              const job = jobs.find((entry) => entry.id === item.job_id);
              return (
                <li key={item.job_id}>
                  <span className="stale-info">
                    <span className="stale-title">{item.company_name} · {item.title}</span>
                    <small>{item.reason}</small>
                  </span>
                  {job && (
                    <button className="small-action" onClick={() => onOpenJob(job)} disabled={taskBusy}>
                      打开岗位
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <form
        className="task-form task-form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          if (!title.trim()) return;
          onAddTask(title.trim(), jobId ? Number(jobId) : undefined, dueDate || undefined).then(() => {
            setTitle("");
            setJobId("");
            setDueDate("");
          });
        }}
      >
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="新增待办" />
        <select value={jobId} onChange={(event) => setJobId(event.target.value)}>
          <option value="">通用任务</option>
          {jobs.map((job) => (
            <option key={job.id} value={job.id}>
              {job.company_name} · {job.title}
            </option>
          ))}
        </select>
        <input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
        <button className="primary-action" disabled={taskBusy}>
          <Plus size={18} />
          添加
        </button>
      </form>
      </div>
      <div className="task-list">
        {sortedTasks(tasks).map((task) => {
          const job = jobs.find((item) => item.id === task.job_id);
          const isDone = task.status === "done";
          return (
            <article key={task.id} className={isDone ? "task-row done" : "task-row"}>
              <button
                className={isDone ? "icon-button marked" : "icon-button"}
                title={isDone ? "标记为待办" : "标记完成"}
                onClick={() => onUpdateTask(task, { status: isDone ? "todo" : "done" })}
                disabled={hasBusy(busy, `task-${task.id}`)}
              >
                {isDone ? <RotateCcw size={16} /> : <CheckCircle2 size={16} />}
              </button>
              <div className="task-main">
                <span className={`status ${task.status}`}>{taskStatusLabels[task.status] ?? task.status}</span>
                <input
                  className="task-title-input"
                  defaultValue={task.title}
                  onBlur={(event) => {
                    const nextTitle = event.currentTarget.value.trim();
                    if (nextTitle && nextTitle !== task.title) onUpdateTask(task, { title: nextTitle });
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                  }}
                />
                <small>{job ? `${job.company_name} · ${job.title}` : "通用任务"}</small>
              </div>
              <div className="task-controls">
                <input
                  type="date"
                  value={task.due_date ?? ""}
                  onChange={(event) => onUpdateTask(task, { due_date: event.target.value || null })}
                  disabled={hasBusy(busy, `task-${task.id}`)}
                  title="截止日期"
                />
                {job && (
                  <button className="small-action" onClick={() => onOpenJob(job)} disabled={taskBusy}>
                    打开岗位
                  </button>
                )}
                <button className="icon-button" title="删除任务" onClick={() => onDeleteTask(task)} disabled={hasBusy(busy, `task-${task.id}`)}>
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          );
        })}
        {!tasks.length && <p className="muted">暂无待办。生成求职冲刺包或手动添加后，会在这里推进。</p>}
      </div>
    </section>
  );
}
