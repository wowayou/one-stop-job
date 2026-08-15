import { RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorMessage, jsonBody } from "../api";
import { PaginationControls } from "../components/PaginationControls";
import { PAGE_SIZE } from "../lib/constants";
import type { Company, Job } from "../types";

export function TrashView({
  trashedJobs,
  trashedCompanies,
  onRefresh,
  onNotify,
}: {
  trashedJobs: Job[];
  trashedCompanies: Company[];
  onRefresh: () => void | Promise<void>;
  onNotify: (kind: "info" | "success" | "warning" | "error", message: string, details?: string[]) => void;
}) {
  const [tab, setTab] = useState<"jobs" | "companies">("jobs");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState<string | null>(null);

  const isJobsTab = tab === "jobs";
  const jobsItems = trashedJobs;
  const companiesItems = trashedCompanies;
  const count = isJobsTab ? jobsItems.length : companiesItems.length;
  const pageCount = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const visibleJobs = jobsItems.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const visibleCompanies = companiesItems.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  async function restoreJob(jobId: number) {
    setBusy(`restore-job-${jobId}`);
    try {
      await api<{ restored: boolean }>(`/api/jobs/${jobId}/restore`, { method: "POST" });
      onNotify("success", "岗位已从回收站恢复。");
      await onRefresh();
    } catch (err) {
      onNotify("error", errorMessage(err, "恢复失败"));
    } finally {
      setBusy(null);
    }
  }

  async function purgeJob(jobId: number) {
    if (!window.confirm("永久删除后无法恢复，确定删除此岗位及其全部关联数据？")) return;
    setBusy(`purge-job-${jobId}`);
    try {
      await api<{ purged: boolean }>(`/api/jobs/${jobId}/purge`, { method: "DELETE" });
      onNotify("success", "岗位已永久删除。");
      await onRefresh();
    } catch (err) {
      onNotify("error", errorMessage(err, "永久删除失败"));
    } finally {
      setBusy(null);
    }
  }

  async function restoreCompany(companyId: number) {
    setBusy(`restore-company-${companyId}`);
    try {
      await api<{ restored: boolean }>(`/api/companies/${companyId}/restore`, { method: "POST" });
      onNotify("success", "公司已从回收站恢复。");
      await onRefresh();
    } catch (err) {
      onNotify("error", errorMessage(err, "恢复失败"));
    } finally {
      setBusy(null);
    }
  }

  async function purgeCompany(companyId: number) {
    if (!window.confirm("永久删除后无法恢复，确定删除此公司记录？关联岗位不会被删除（公司链接会被清除）。")) return;
    setBusy(`purge-company-${companyId}`);
    try {
      await api<{ purged: boolean }>(`/api/companies/${companyId}/purge`, { method: "DELETE" });
      onNotify("success", "公司已永久删除。");
      await onRefresh();
    } catch (err) {
      onNotify("error", errorMessage(err, "永久删除失败"));
    } finally {
      setBusy(null);
    }
  }

  async function purgeAll() {
    if (!count) return;
    if (!window.confirm(`将永久删除回收站里的全部 ${count} 个${isJobsTab ? "岗位" : "公司"}，无法恢复，确定？`)) return;
    setBusy("purge-all");
    try {
      if (isJobsTab) {
        for (const job of jobsItems) {
          await api(`/api/jobs/${job.id}/purge`, { method: "DELETE" });
        }
      } else {
        for (const company of companiesItems) {
          await api(`/api/companies/${company.id}/purge`, { method: "DELETE" });
        }
      }
      onNotify("success", `已清空回收站（${count} 项永久删除）。`);
      await onRefresh();
    } catch (err) {
      onNotify("error", errorMessage(err, "清空回收站失败"));
      await onRefresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="content-panel trash-panel">
      <div className="list-summary">
        <strong>回收站</strong>
        <div className="row-actions">
          <button
            className="small-action danger-text"
            onClick={purgeAll}
            disabled={!count || busy === "purge-all"}
          >
            <Trash2 size={14} />
            清空回收站
          </button>
        </div>
      </div>

      <div className="segmented trash-tabs">
        <button className={tab === "jobs" ? "active" : ""} onClick={() => { setTab("jobs"); setPage(1); }}>
          岗位（{trashedJobs.length}）
        </button>
        <button className={tab === "companies" ? "active" : ""} onClick={() => { setTab("companies"); setPage(1); }}>
          公司（{trashedCompanies.length}）
        </button>
      </div>

      {!count ? (
        <div className="empty-cell">{isJobsTab ? "回收站里没有岗位" : "回收站里没有公司"}</div>
      ) : (
        <>
          {isJobsTab &&
            visibleJobs.map((job) => (
              <div className="trash-item" key={job.id}>
                <div className="trash-item-info">
                  <strong>{job.title}</strong>
                  <span>{job.company_name} · {job.area || job.city || "-"} · 删除于 {job.deleted_at?.slice(0, 10) ?? "-"}</span>
                </div>
                <div className="trash-item-actions">
                  <button className="small-action" onClick={() => restoreJob(job.id)} disabled={busy === `restore-job-${job.id}`}>
                    <RotateCcw size={14} />
                    恢复
                  </button>
                  <button className="small-action danger-text" onClick={() => purgeJob(job.id)} disabled={busy === `purge-job-${job.id}`}>
                    <Trash2 size={14} />
                    永久删除
                  </button>
                </div>
              </div>
            ))}
          {!isJobsTab &&
            visibleCompanies.map((company) => (
              <div className="trash-item" key={company.id}>
                <div className="trash-item-info">
                  <strong>{company.name}</strong>
                  <span>{company.industry || "-"} · 删除于 {company.deleted_at?.slice(0, 10) ?? "-"}</span>
                </div>
                <div className="trash-item-actions">
                  <button className="small-action" onClick={() => restoreCompany(company.id)} disabled={busy === `restore-company-${company.id}`}>
                    <RotateCcw size={14} />
                    恢复
                  </button>
                  <button className="small-action danger-text" onClick={() => purgeCompany(company.id)} disabled={busy === `purge-company-${company.id}`}>
                    <Trash2 size={14} />
                    永久删除
                  </button>
                </div>
              </div>
            ))}
          <div className="list-footer">
            <PaginationControls page={page} total={count} pageSize={PAGE_SIZE} onPage={setPage} />
          </div>
        </>
      )}
    </section>
  );
}
