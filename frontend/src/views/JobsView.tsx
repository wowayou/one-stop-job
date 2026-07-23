import { CheckCircle2, Download, Inbox, Pin, RotateCcw, Search, Star, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { PaginationControls } from "../components/PaginationControls";
import { ScoreChip } from "../components/ScoreChip";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { JOB_PAGE_SIZE, jobStatuses, statusLabels, statuses } from "../lib/constants";
import type { Job } from "../types";

export function JobsView({
  jobs,
  search,
  status,
  source,
  sources,
  sort,
  onSearch,
  onStatus,
  onSource,
  onSort,
  onOpen,
  onPatch,
  onBulkPatch,
  onScoreJob,
  busy,
  onExport
}: {
  jobs: Job[];
  search: string;
  status: string;
  source: string;
  sources: string[];
  sort: "default" | "score";
  onSearch: (value: string) => void;
  onStatus: (value: string) => void;
  onSource: (value: string) => void;
  onSort: (value: "default" | "score") => void;
  onOpen: (job: Job) => void;
  onPatch: (job: Job, updates: Partial<Job>) => Promise<void>;
  onBulkPatch: (ids: number[], updates: Pick<Partial<Job>, "status" | "favorite">) => Promise<void>;
  onScoreJob: (jobId: number) => Promise<void>;
  busy: BusyState;
  onExport: () => Promise<void>;
}) {
  const [page, setPage] = useState(1);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  useEffect(() => {
    setPage(1);
    setSelectedIds(new Set());
  }, [jobs.length, search, status, source, sort]);
  const pageCount = Math.max(1, Math.ceil(jobs.length / JOB_PAGE_SIZE));
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);
  const visibleJobs = jobs.slice((page - 1) * JOB_PAGE_SIZE, page * JOB_PAGE_SIZE);
  const filteredIds = jobs.map((job) => job.id);
  const visibleIds = visibleJobs.map((job) => job.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const allFilteredSelected = filteredIds.length > 0 && filteredIds.every((id) => selectedIds.has(id));
  const selectedCount = selectedIds.size;
  const bulkBusy = hasBusy(busy, "bulk");

  function toggleJob(jobId: number, checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(jobId);
      else next.delete(jobId);
      return next;
    });
  }

  function toggleVisible(checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      for (const id of visibleIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  function toggleFiltered(checked: boolean) {
    setSelectedIds(checked ? new Set(filteredIds) : new Set());
  }

  async function runBulk(updates: Pick<Partial<Job>, "status" | "favorite">) {
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    await onBulkPatch(ids, updates);
    setSelectedIds(new Set());
  }

  return (
    <section className="content-panel jobs-panel">
      <div className="filterbar">
        <div className="searchbox">
          <Search size={18} />
          <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索岗位、公司、区域、技能" />
        </div>
        {sources.length > 1 && (
          <select className="source-select" value={source} onChange={(event) => onSource(event.target.value)}>
            <option value="all">全部来源</option>
            {sources.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        )}
        <select className="source-select" value={status} onChange={(event) => onStatus(event.target.value)} title="按状态筛选">
          {statuses.map((item) => (
            <option key={item} value={item}>
              {statusLabels[item]}
            </option>
          ))}
        </select>
        <div className="segmented" title="排序方式">
          <button className={sort === "default" ? "active" : ""} onClick={() => onSort("default")}>
            默认
          </button>
          <button className={sort === "score" ? "active" : ""} onClick={() => onSort("score")}>
            评分↓
          </button>
        </div>
        <div className="filterbar-end">
          <span className="muted">共 {jobs.length} 个</span>
          <button type="button" className="icon-button" onClick={onExport} title="按当前筛选导出 CSV">
            <Download size={16} />
          </button>
        </div>
      </div>
      {selectedCount > 0 && (
      <div className="bulkbar">
        <span>已选 {selectedCount} 个 / 当前匹配 {jobs.length} 个</span>
        <div className="bulk-actions">
          <button className="small-action" onClick={() => toggleFiltered(!allFilteredSelected)} disabled={!jobs.length || bulkBusy}>
            <CheckCircle2 size={14} />
            {allFilteredSelected ? "取消全选" : `选择全部匹配 ${jobs.length} 个`}
          </button>
          <button className="small-action" onClick={() => setSelectedIds(new Set())} disabled={!selectedCount || bulkBusy}>
            <X size={14} />
            清空选择
          </button>
          <button className="small-action" onClick={() => runBulk({ status: "researching" })} disabled={!selectedCount || bulkBusy}>
            <Search size={14} />
            待调研
          </button>
          <button className="small-action" onClick={() => runBulk({ status: "fit" })} disabled={!selectedCount || bulkBusy}>
            <Star size={14} />
            高潜
          </button>
          <button className="small-action" onClick={() => runBulk({ status: "rejected" })} disabled={!selectedCount || bulkBusy}>
            <Trash2 size={14} />
            拒绝
          </button>
          <button className="small-action" onClick={() => runBulk({ status: "archived" })} disabled={!selectedCount || bulkBusy}>
            <Inbox size={14} />
            归档
          </button>
          <button className="small-action" onClick={() => runBulk({ favorite: true })} disabled={!selectedCount || bulkBusy}>
            <Pin size={14} />
            置顶
          </button>
          <button className="small-action" onClick={() => runBulk({ favorite: false })} disabled={!selectedCount || bulkBusy}>
            <RotateCcw size={14} />
            取消置顶
          </button>
        </div>
      </div>
      )}
      <div className="job-grid-shell" role="table" aria-label="岗位池列表">
        <div className="job-grid-header" role="row">
          <div className="job-grid-cell select-cell" role="columnheader">
            <input
              type="checkbox"
              checked={allVisibleSelected}
              disabled={!visibleIds.length}
              onChange={(event) => toggleVisible(event.target.checked)}
              aria-label="选择本页岗位"
            />
          </div>
          <div className="job-grid-cell job-col-score" role="columnheader">评分</div>
          <div className="job-grid-cell job-col-title" role="columnheader">岗位</div>
          <div className="job-grid-cell job-col-company" role="columnheader">公司</div>
          <div className="job-grid-cell job-col-salary" role="columnheader">薪资</div>
          <div className="job-grid-cell job-col-location" role="columnheader">地点</div>
          <div className="job-grid-cell job-col-status" role="columnheader">状态</div>
        </div>
        <div className="job-grid-body" role="rowgroup">
          {visibleJobs.map((job) => (
            <div
              key={job.id}
              className={selectedIds.has(job.id) ? "job-grid-row selected" : "job-grid-row"}
              role="row"
              onClick={() => onOpen(job)}
            >
              <div className="job-grid-cell select-cell" role="cell" onClick={(event) => event.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(job.id)}
                  onChange={(event) => toggleJob(job.id, event.target.checked)}
                  aria-label={`选择 ${job.company_name} ${job.title}`}
                />
              </div>
              <div className="job-grid-cell job-col-score" role="cell" data-label="评分">
                <ScoreChip job={job} busy={busy} onScoreJob={onScoreJob} />
              </div>
              <div className="job-grid-cell job-col-title primary-cell" role="cell" data-label="岗位">
                <button
                  type="button"
                  className={job.favorite ? "fav-toggle marked" : "fav-toggle"}
                  title={job.favorite ? "取消置顶" : "置顶"}
                  aria-label={job.favorite ? "取消置顶" : "置顶"}
                  onClick={(event) => {
                    event.stopPropagation();
                    onPatch(job, { favorite: !job.favorite });
                  }}
                >
                  <Pin size={14} />
                </button>
                <strong>{job.title}</strong>
              </div>
              <div className="job-grid-cell job-col-company" role="cell" data-label="公司">{job.company_name}</div>
              <div className="job-grid-cell job-col-salary" role="cell" data-label="薪资">{job.salary_text || "-"}</div>
              <div className="job-grid-cell job-col-location" role="cell" data-label="地点">{job.area || job.city || "-"}</div>
              <div className="job-grid-cell job-col-status" role="cell" data-label="状态" onClick={(event) => event.stopPropagation()}>
                <select
                  className={`status-select status ${job.status}`}
                  value={job.status}
                  onChange={(event) => onPatch(job, { status: event.target.value })}
                  title="切换岗位状态"
                >
                  {jobStatuses.map((item) => (
                    <option key={item} value={item}>
                      {statusLabels[item]}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ))}
          {!jobs.length && <div className="job-grid-empty">暂无岗位数据</div>}
        </div>
      </div>
      <div className="list-footer">
        <PaginationControls page={page} total={jobs.length} pageSize={JOB_PAGE_SIZE} onPage={setPage} />
      </div>
    </section>
  );
}
