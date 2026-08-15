import { Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { PaginationControls } from "../components/PaginationControls";
import { PAGE_SIZE } from "../lib/constants";
import type { Company, Job } from "../types";

export function CompaniesView({
  companies,
  jobs,
  onOpenJob,
  onDelete,
}: {
  companies: Company[];
  jobs: Job[];
  onOpenJob: (job: Job) => void;
  onDelete?: (company: Company) => void;
}) {
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [companies.length]);
  const visibleCompanies = companies.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  return (
    <section className="content-panel companies-panel">
      <div className="list-summary">
        <strong>公司调研</strong>
        <div className="row-actions">
          <PaginationControls page={page} total={companies.length} pageSize={PAGE_SIZE} onPage={setPage} />
        </div>
      </div>
      <div className="grid-list">
        {visibleCompanies.map((company) => {
          const companyJobs = jobs.filter((job) => job.company_id === company.id);
          return (
            <article className="company-item" key={company.id}>
              <div className="company-item-head-row">
                <div>
                  <h3>{company.name}</h3>
                  <p>{[company.industry, company.size, company.stage].filter(Boolean).join(" · ") || "公司画像待补充"}</p>
                </div>
                {onDelete && (
                  <button className="icon-button compact danger-text" title="移入回收站" onClick={() => onDelete(company)}>
                    <Trash2 size={15} />
                  </button>
                )}
              </div>
              <div className="company-meta">
                <span>{company.jobs_count ?? companyJobs.length} 岗位</span>
                <span>{company.evidence_count ?? 0} 证据</span>
                <span className={`risk ${company.risk_level}`}>{company.risk_level}</span>
              </div>
              <div className="chip-row">
                {companyJobs.slice(0, 3).map((job) => (
                  <button key={job.id} className="chip" onClick={() => onOpenJob(job)}>
                    {job.title}
                  </button>
                ))}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
