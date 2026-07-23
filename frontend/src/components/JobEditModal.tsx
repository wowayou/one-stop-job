import { CheckCircle2, X } from "lucide-react";
import { FormEvent, useState } from "react";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { jobToEditForm } from "../lib/format";
import type { Job, JobEditForm } from "../types";

export function JobEditModal({
  job,
  busy,
  onClose,
  onSave
}: {
  job: Job;
  busy: BusyState;
  onClose: () => void;
  onSave: (form: JobEditForm) => Promise<void>;
}) {
  useEscapeClose(true, onClose);
  const [form, setForm] = useState<JobEditForm>(() => jobToEditForm(job));

  function update<K extends keyof JobEditForm>(key: K, value: JobEditForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSave(form);
  }

  return (
    <div className="modal-backdrop">
      <form className="modal job-edit-modal" onSubmit={submit}>
        <div className="modal-head">
          <h2>编辑岗位</h2>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="form-grid">
          <label>
            岗位
            <input value={form.title} onChange={(event) => update("title", event.target.value)} required />
          </label>
          <label>
            公司
            <input value={form.company_name} onChange={(event) => update("company_name", event.target.value)} required />
          </label>
          <label>
            原链接
            <input type="url" value={form.url} onChange={(event) => update("url", event.target.value)} placeholder="https://..." />
          </label>
          <label>
            薪资
            <input value={form.salary_text} onChange={(event) => update("salary_text", event.target.value)} placeholder="8-12K" />
          </label>
          <label>
            城市
            <input value={form.city} onChange={(event) => update("city", event.target.value)} />
          </label>
          <label>
            区域
            <input value={form.area} onChange={(event) => update("area", event.target.value)} />
          </label>
          <label>
            经验
            <input value={form.experience} onChange={(event) => update("experience", event.target.value)} />
          </label>
          <label>
            学历
            <input value={form.degree} onChange={(event) => update("degree", event.target.value)} />
          </label>
          <label>
            招聘人
            <input value={form.recruiter} onChange={(event) => update("recruiter", event.target.value)} />
          </label>
          <label>
            发布时间
            <input type="date" value={form.published_at} onChange={(event) => update("published_at", event.target.value)} />
          </label>
          <label>
            招聘状态
            <select value={form.recruitment_status} onChange={(event) => update("recruitment_status", event.target.value)}>
              <option value="unknown">未知</option>
              <option value="active">在招</option>
              <option value="closed">已关闭</option>
            </select>
          </label>
          <label>
            技能
            <input value={form.skills} onChange={(event) => update("skills", event.target.value)} />
          </label>
        </div>
        <label>
          JD / 描述
          <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
        </label>
        <button className="primary-action" disabled={hasBusy(busy, "edit-job")}>
          <CheckCircle2 size={18} />
          保存修改
        </button>
      </form>
    </div>
  );
}
