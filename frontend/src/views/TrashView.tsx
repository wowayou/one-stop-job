import { RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorMessage } from "../api";
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
  const items = isJobsTab ? trashedJobs : trashedCompanies;
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const visible = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  async function restoreJob(id: number) {
    setBusy(`restore-${id}`);
    try {
      await api<{ restored: boolean }>(`/api/jobs/${id}/restore`, { method: "POST" });
      onNotify("success", "已恢复。");
      await onRefresh();
    } catch (err) { onNotify("error", errorMessage(err, "恢复失败")); }
    finally { setBusy(null); }
  }

  async function purgeJob(id: number) {
    if (!window.confirm("永久删除后无法恢复，确定？")) return;
    setBusy(`purge-${id}`);
    try {
      await api(`/api/jobs/${id}/purge`, { method: "DELETE" });
      onNotify("success", "已永久删除。");
      await onRefresh();
    } catch (err) { onNotify("error", errorMessage(err, "删除失败")); }
    finally { setBusy(null); }
  }

  async function restoreCompany(id: number) {
    setBusy(`restore-${id}`);
    try {
      await api<{ restored: boolean }>(`/api/companies/${id}/restore`, { method: "POST" });
      onNotify("success", "已恢复。");
      await onRefresh();
    } catch (err) { onNotify("error", errorMessage(err, "恢复失败")); }
    finally { setBusy(null); }
  }

  async function purgeCompany(id: number) {
    if (!window.confirm("永久删除后无法恢复，确定？")) return;
    setBusy(`purge-${id}`);
    try {
      await api(`/api/companies/${id}/purge`, { method: "DELETE" });
      onNotify("success", "已永久删除。");
      await onRefresh();
    } catch (err) { onNotify("error", errorMessage(err, "删除失败")); }
    finally { setBusy(null); }
  }

  async function purgeAll() {
    if (!items.length) return;
    if (!window.confirm(`将永久删除全部 ${items.length} 个${isJobsTab ? "岗位" : "公司"}，无法恢复，确定？`)) return;
    setBusy("purge-all");
    try {
      if (isJobsTab) {
        for (const j of trashedJobs) await api(`/api/jobs/${j.id}/purge`, { method: "DELETE" });
      } else {
        for (const c of trashedCompanies) await api(`/api/companies/${c.id}/purge`, { method: "DELETE" });
      }
      onNotify("success", `已清空（${items.length} 项）。`);
      await onRefresh();
    } catch (err) { onNotify("error", errorMessage(err, "清空失败")); await onRefresh(); }
    finally { setBusy(null); }
  }

  return (
    <section className="content-panel trash-panel">
      {/* 固定头部：标题 + tabs + 清空按钮 */}
      <div className="trash-head">
        <div className="trash-head-top">
          <strong>回收站</strong>
          <button className="small-action danger-text" onClick={purgeAll} disabled={!items.length || busy === "purge-all"}>
            <Trash2 size={14} />
            清空回收站
          </button>
        </div>
        <div className="segmented trash-tabs">
          <button className={isJobsTab ? "active" : ""} onClick={() => { setTab("jobs"); setPage(1); }}>
            岗位（{trashedJobs.length}）
          </button>
          <button className={!isJobsTab ? "active" : ""} onClick={() => { setTab("companies"); setPage(1); }}>
            公司（{trashedCompanies.length}）
          </button>
        </div>
      </div>
      {/* 可滚动列表区 */}
      <div className="trash-list-scroll">
        {!visible.length ? (
          <div className="empty-cell">{isJobsTab ? "回收站里没有岗位" : "回收站里没有公司"}</div>
        ) : (
          <div className="trash-list">
            {visible.map((item) => {
              const id = item.id;
              const name = isJobsTab ? (item as Job).title : (item as Company).name;
              const sub = isJobsTab
                ? `${(item as Job).company_name} · ${(item as Job).area || (item as Job).city || "-"}`
                : (item as Company).industry || "-";
              const deletedAt = item.deleted_at?.slice(0, 10) ?? "-";
              return (
                <div className="trash-item" key={id}>
                  <div className="trash-item-info">
                    <strong>{name}</strong>
                    <span>{sub} · 删除于 {deletedAt}</span>
                  </div>
                  <div className="trash-item-actions">
                    <button className="small-action" onClick={() => isJobsTab ? restoreJob(id) : restoreCompany(id)} disabled={busy === `restore-${id}`}>
                      <RotateCcw size={14} />
                      恢复
                    </button>
                    <button className="small-action danger-text" onClick={() => isJobsTab ? purgeJob(id) : purgeCompany(id)} disabled={busy === `purge-${id}`}>
                      <Trash2 size={14} />
                      永久删除
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {/* 固定底部：分页 */}
      {items.length > PAGE_SIZE && (
        <div className="trash-foot">
          <PaginationControls page={page} total={items.length} pageSize={PAGE_SIZE} onPage={setPage} />
        </div>
      )}
    </section>
  );
}