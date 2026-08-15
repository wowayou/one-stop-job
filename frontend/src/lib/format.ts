import type { AiStatus, FollowUpTask, IngestCandidate, InterviewLog, Job, JobEditForm, SourceRun } from "../types";
import { draftKindLabels, OPPORTUNITY_DIMENSIONS } from "./constants";

export function sumOpportunity(details: Record<string, number>) {
  return OPPORTUNITY_DIMENSIONS.reduce((sum, dim) => sum + (Number(details?.[dim.key]) || 0), 0);
}

export function concludeOpportunity(total: number) {
  if (total >= 80) return "重点推进";
  if (total >= 65) return "继续观察";
  if (total >= 50) return "保底";
  return "放弃";
}

// 把一条复盘按"面试复盘模板"拼成 Markdown，配合 copyToClipboard 一键导出。
export function interviewLogToMarkdown(log: InterviewLog, job?: Job | null) {
  const dims = OPPORTUNITY_DIMENSIONS.map((dim) => `- ${dim.key}：${log.score_details?.[dim.key] ?? "-"} / ${dim.weight}`).join("\n");
  return [
    "# 面试复盘",
    "",
    `- 公司：${job?.company_name ?? "-"}`,
    `- 岗位：${job?.title ?? "-"}`,
    `- 轮次：${log.round}`,
    `- 日期：${log.interview_date ?? "-"}`,
    `- 面试官：${log.interviewer ?? "-"}`,
    "",
    "## 机会评分",
    dims,
    `- 总分：${log.opportunity_score ?? "-"} / 100`,
    `- 结论：${log.conclusion || "-"}`,
    "",
    "## 岗位真实画像",
    log.real_picture || "-",
    "",
    "## 面试问题复盘",
    log.qa_review || "-",
    "",
    "## 暴露的短板",
    log.weaknesses || "-",
    "",
    "## 下一步动作",
    log.next_actions || "-",
    "",
    "## 跟进话术 / 动作",
    log.follow_up || "-"
  ].join("\n");
}

export function jobToEditForm(job: Job): JobEditForm {
  return {
    title: job.title ?? "",
    company_name: job.company_name ?? "",
    url: job.url ?? "",
    salary_text: job.salary_text ?? "",
    city: job.city ?? "",
    area: job.area ?? "",
    experience: job.experience ?? "",
    degree: job.degree ?? "",
    skills: job.skills ?? "",
    description: job.description ?? "",
    recruiter: job.recruiter ?? "",
    published_at: job.published_at ?? "",
    recruitment_status: job.recruitment_status ?? "unknown"
  };
}

export function optionalText(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function jobEditPayload(form: JobEditForm): Partial<Job> {
  return {
    title: form.title.trim(),
    company_name: form.company_name.trim(),
    url: optionalText(form.url),
    salary_text: optionalText(form.salary_text),
    city: optionalText(form.city),
    area: optionalText(form.area),
    experience: optionalText(form.experience),
    degree: optionalText(form.degree),
    skills: optionalText(form.skills),
    description: optionalText(form.description),
    recruiter: optionalText(form.recruiter),
    published_at: optionalText(form.published_at),
    recruitment_status: form.recruitment_status || "unknown"
  };
}

export function skippedItems(run?: SourceRun | null) {
  return run?.raw_config?.skipped ?? [];
}

export function runDetailLines(run: SourceRun, fallback?: string) {
  const details: string[] = [];
  const report = run.raw_config;
  if (report?.urls_total != null) {
    details.push(`页面/链接解析：${report.urls_ok ?? 0}/${report.urls_total} 个成功。`);
  }
  if (report?.jobs != null) {
    details.push(`解析岗位：${report.jobs} 个。`);
  }
  // 区域白名单挡掉的条数必须露出来：否则「抓了 89 条却只剩 12 条待筛」看着像丢数据。
  if (report?.area_filter?.enabled && report.area_filter.filtered) {
    const unknown = report.area_filter.unknown_area
      ? `（其中 ${report.area_filter.unknown_area} 条区域未知）`
      : "";
    details.push(`区域白名单过滤：${report.area_filter.filtered} 条${unknown}。`);
  }
  if (report?.already_pending) {
    details.push(`已在待筛列表或此前跳过：${report.already_pending} 条，本次不再重复列出。`);
  }
  for (const item of skippedItems(run)) {
    const reason = item.reason || "未说明原因";
    details.push(item.url ? `${item.url}：${reason}` : reason);
  }
  if (!details.length && fallback) details.push(fallback);
  return details;
}

/** 采集运行的一行计数摘要：初筛口径（抓取 / 待筛 / 刷新），不再报「新增」——采集器不再直接建 Job。 */
export function runCountsText(run: SourceRun) {
  const report = run.raw_config;
  const parts = [`${run.fetched_count} 抓取`];
  if (report?.area_filter?.enabled && report.area_filter.filtered) {
    parts.push(`${report.area_filter.filtered} 区域过滤`);
  }
  if (report?.pending != null) parts.push(`${report.pending} 待筛`);
  else if (run.created_count) parts.push(`${run.created_count} 新增`);
  if (run.updated_count) parts.push(`${run.updated_count} 刷新`);
  return parts.join(" / ");
}

export function runHasNoJobs(run: SourceRun) {
  return run.fetched_count === 0 && run.created_count === 0 && run.updated_count === 0;
}

export function jobSourceLabels(job: Job) {
  return Array.from(new Set([job.source, ...(job.source_links ?? []).map((link) => link.source)].filter(Boolean)));
}

export function draftKindLabel(kind: string) {
  return draftKindLabels[kind] ?? kind;
}

export function sortedTasks(tasks: FollowUpTask[]) {
  return [...tasks].sort((a, b) => {
    const doneDelta = Number(a.status === "done") - Number(b.status === "done");
    if (doneDelta) return doneDelta;
    const aDue = a.due_date ?? "9999-12-31";
    const bDue = b.due_date ?? "9999-12-31";
    if (aDue !== bDue) return aDue.localeCompare(bDue);
    return b.id - a.id;
  });
}

export function rankedJobs(jobs: Job[]) {
  return [...jobs].sort((a, b) => {
    const aScore = a.latest_score?.total ?? -1;
    const bScore = b.latest_score?.total ?? -1;
    const aBlocked = a.latest_score?.hard_blocked ? 1 : 0;
    const bBlocked = b.latest_score?.hard_blocked ? 1 : 0;
    if (aBlocked !== bBlocked) return aBlocked - bBlocked;
    if (aScore !== bScore) return bScore - aScore;
    if (a.favorite !== b.favorite) return Number(b.favorite) - Number(a.favorite);
    return b.id - a.id;
  });
}

export function aiStatusLabel(status: AiStatus | null) {
  if (!status) return "读取中";
  if (!status.enabled_in_config) return "未启用";
  if (!status.api_key_configured) return "待配置";
  return status.available ? "可用" : "不可用";
}

export function buildInboxLinePreview(candidate: IngestCandidate): string {
  const flatten = (value?: string | null) => (value || "").replace(/\s+/g, " ").trim();
  const company = flatten(candidate.company_name) || "?";
  const title = flatten(candidate.title);
  const salary = flatten(candidate.salary_text) || "薪资未知";
  const source = candidate.source || "manual";
  const now = new Date();
  const dateTag = `${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
  return `- [ ] ${company} - ${title} - ${salary} - ${source}/${dateTag} - 未判断 - 下一步：补齐主行并新建详情卡`;
}

export function asConfigMap(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function asConfigArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map((item) => asConfigMap(item)) : [];
}

export function stringValue(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

export function numberValue(value: unknown, fallback = 0) {
  return typeof value === "number" ? value : fallback;
}

export function booleanValue(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

export function linesValue(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "";
}

export function splitLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function setConfigValue(config: Record<string, unknown>, path: string[], value: unknown) {
  const next = { ...config };
  let cursor: Record<string, unknown> = next;
  path.forEach((part, index) => {
    if (index === path.length - 1) {
      cursor[part] = value;
      return;
    }
    const child = { ...asConfigMap(cursor[part]) };
    cursor[part] = child;
    cursor = child;
  });
  return next;
}

// 评分等级色阶：与 backend 评分区间保持一致，供表格评分芯片、岗位详情、面试机会评分和冲刺包共用。
export function scoreClass(score?: number | null) {
  if (score == null) return "score-pill";
  if (score >= 80) return "score-pill high";
  if (score >= 65) return "score-pill mid";
  return "score-pill low";
}
