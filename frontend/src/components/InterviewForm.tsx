import { Plus } from "lucide-react";
import { useState } from "react";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { EMPTY_INTERVIEW_FORM, FOLLOWUP_TEMPLATES, INTERVIEW_ROUNDS, OPPORTUNITY_DIMENSIONS } from "../lib/constants";
import { concludeOpportunity, scoreClass, sumOpportunity } from "../lib/format";
import type { InterviewLog } from "../types";

export function InterviewForm({ busy, onAdd }: { busy: BusyState; onAdd: (payload: Partial<InterviewLog>) => Promise<void> }) {
  const [form, setForm] = useState(EMPTY_INTERVIEW_FORM);
  const total = sumOpportunity(form.score_details);
  const conclusion = concludeOpportunity(total);
  const scored = Object.keys(form.score_details).length > 0;
  return (
    <form
      className="research-form interview-form"
      onSubmit={(event) => {
        event.preventDefault();
        onAdd({
          round: form.round,
          interview_date: form.interview_date || null,
          interviewer: form.interviewer || null,
          real_picture: form.real_picture,
          score_details: form.score_details,
          opportunity_score: scored ? total : null,
          conclusion: scored ? conclusion : "",
          qa_review: form.qa_review,
          weaknesses: form.weaknesses,
          next_actions: form.next_actions,
          follow_up: form.follow_up
        }).then(() => setForm(EMPTY_INTERVIEW_FORM));
      }}
    >
      <div className="interview-form-head">
        <select value={form.round} onChange={(event) => setForm({ ...form, round: event.target.value })} title="轮次">
          {INTERVIEW_ROUNDS.map((round) => (
            <option key={round}>{round}</option>
          ))}
        </select>
        <input type="date" value={form.interview_date} onChange={(event) => setForm({ ...form, interview_date: event.target.value })} title="面试日期" />
        <input value={form.interviewer} placeholder="面试官身份" onChange={(event) => setForm({ ...form, interviewer: event.target.value })} />
      </div>

      <div className="opportunity-grid">
        {OPPORTUNITY_DIMENSIONS.map((dim) => (
          <label key={dim.key} className="opportunity-dim">
            <span>
              {dim.key}
              <small>/{dim.weight}</small>
            </span>
            <input
              type="number"
              min={0}
              max={dim.weight}
              step={1}
              value={form.score_details[dim.key] ?? ""}
              onChange={(event) => {
                const raw = event.target.value;
                const next = { ...form.score_details };
                if (raw === "") delete next[dim.key];
                else next[dim.key] = Math.max(0, Math.min(dim.weight, Math.round(Number(raw))));
                setForm({ ...form, score_details: next });
              }}
            />
          </label>
        ))}
      </div>
      <p className="opportunity-total">
        机会评分 <strong className={scoreClass(scored ? total : null)}>{scored ? total : "-"}</strong> / 100 · 结论：<strong>{scored ? conclusion : "未评分"}</strong>
      </p>

      <textarea
        value={form.real_picture}
        placeholder="岗位真实画像：对方到底想招什么人（SEO/SEM/平台/内容）？是否甲方、有无独立站、GSC/GA4/Ads/询盘数据、团队配合、加班与管理方式。"
        onChange={(event) => setForm({ ...form, real_picture: event.target.value })}
      />
      <textarea
        value={form.qa_review}
        placeholder="面试问题复盘：问题 | 我当时怎么答 | 问题所在 | 下次更好的回答"
        onChange={(event) => setForm({ ...form, qa_review: event.target.value })}
      />
      <textarea
        value={form.weaknesses}
        placeholder="暴露的短板：知识 / 案例 / 表达 / 经验"
        onChange={(event) => setForm({ ...form, weaknesses: event.target.value })}
      />
      <textarea
        value={form.next_actions}
        placeholder="下一步动作：简历改哪一句 / 下次要补的案例 / 下次要追问的问题"
        onChange={(event) => setForm({ ...form, next_actions: event.target.value })}
      />

      <div className="followup-templates">
        {FOLLOWUP_TEMPLATES.map((tpl) => (
          <button type="button" key={tpl.label} className="small-action" onClick={() => setForm((prev) => ({ ...prev, follow_up: tpl.text }))}>
            填入「{tpl.label}」话术
          </button>
        ))}
      </div>
      <textarea
        value={form.follow_up}
        placeholder="跟进话术 / 动作（可点上方模板一键填入再按公司改）"
        onChange={(event) => setForm({ ...form, follow_up: event.target.value })}
      />

      <button className="primary-action" disabled={hasBusy(busy, "interview")}>
        <Plus size={16} />
        保存复盘
      </button>
    </form>
  );
}
