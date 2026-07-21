import {
  AlertCircle,
  AlertTriangle,
  BriefcaseBusiness,
  Building2,
  CalendarCheck,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Copy,
  Download,
  FileQuestion,
  Gauge,
  Globe,
  Inbox,
  Info,
  ImagePlus,
  Loader2,
  MessageSquareText,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  SlidersHorizontal,
  Star,
  Trash2,
  Upload,
  X
} from "lucide-react";
import { ClipboardEvent, FormEvent, RefObject, useEffect, useMemo, useRef, useState } from "react";
import { api, apiUrl, copyToClipboard, downloadApiFile, errorMessage, jsonBody } from "./api";
import { hasAnyBusy, hasBusy, type BusyState, useBusyState } from "./hooks/useBusyState";
import { isTypingElement, useEscapeClose } from "./hooks/useEscapeClose";
import Tour, { TourStep } from "./Tour";
import type { AiProbeResult, AiStatus, AppConfig, ApplicationEvent, ChatContextPreview, ChatMessage, ChatReply, ChatThread, ChatThreadDetail, Company, DecisionAnalysis, Draft, FitScore, FollowUpTask, FunnelAnalytics, IngestCandidate, InterviewLog, InterviewPrep, Job, JobBulkUpdateResult, JobSourceStatus, ResearchItem, SourceRun, SprintBrief, StaleJob, UserProfile } from "./types";

// 聚光灯引导步骤：只指向首屏稳定存在的元素，避免切视图编排，保持简单稳健。
const TOUR_STEPS: TourStep[] = [
  {
    title: "欢迎使用 job-one-stop",
    body: "这是本地优先的个人求职助手：先在决策聊天里丢材料，再管理岗位、公司调研、跟进和面试准备。数据只存本机，不自动投递、不自动发消息。下面用 30 秒带你认识主要区域。"
  },
  { target: "nav", title: "主导航", body: "拿不准时先用决策聊天；明确要推进后，再到岗位池、公司调研、面试准备和待办完成闭环。" },
  { target: "metrics", title: "概览与漏斗", body: "顶部一条统计带:岗位总数、高潜、待调研、最高分、草稿,以及已投/面试/Offer/待跟进漏斗。点数字会跳到对应视图。" },
  { target: "collect", title: "采集与导入", body: "这一排按钮负责补充真实岗位：运行 BOSS 采集、抓 beBee、导入 CSV/XLSX。返回 0 岗位时先看通知里的跳过原因。" },
  { target: "wechat", title: "公众号 / 元宝导入", body: "粘贴元宝回答或 mp.weixin 链接，系统会抓正文并拆出多个岗位；被风控的文章可改为手动粘正文。" },
  { target: "sprint", title: "今日求职冲刺包", body: "一键补评分、挑 Top 岗位、生成面试准备并建待办，最后给出可复制的 Markdown 清单。" },
  { target: "manual", title: "新增岗位", body: "没有链接时也能手动单条录入岗位。所有来源都会汇入同一条管线并跨来源去重。" },
  { target: "guide", title: "随时回看", body: "需要再看这份引导时，点这个信息按钮就能重新开始。准备好了就开始今天的求职推进吧。" }
];

const navItems = [
  { id: "chat", label: "聊天", icon: MessageSquareText },
  { id: "jobs", label: "岗位", icon: BriefcaseBusiness },
  { id: "companies", label: "公司", icon: Building2 },
  { id: "prep", label: "准备", icon: FileQuestion },
  { id: "interviews", label: "复盘", icon: NotebookPen },
  { id: "tasks", label: "待办", icon: CalendarCheck },
  { id: "config", label: "设置", icon: SlidersHorizontal }
] as const;

const statuses = ["all", "new", "researching", "fit", "applied", "interview", "offer", "rejected", "archived"];
const jobStatuses = statuses.filter((item) => item !== "all");
const statusLabels: Record<string, string> = {
  all: "全部",
  new: "新增",
  researching: "待调研",
  fit: "合适",
  applied: "已投递",
  interview: "面试",
  offer: "Offer",
  rejected: "拒绝",
  archived: "归档"
};

const sourceTypes = ["company_site", "job_post", "search", "xhs", "maimai", "kanzhun", "manual_note"];
const draftKindLabels: Record<string, string> = {
  boss_message: "沟通草稿",
  communication_draft: "沟通草稿",
  core_pitch: "核心优势话术",
  tailored_resume: "对应简历"
};

const YUANBAO_PROMPT =
  "帮我找最近【请填写目标城市和目标岗位】相关的招聘公众号文章。\n" +
  "只输出一个 JSON 数组，每项含 title 和 link 两个字段；\n" +
  "link 必须是以 https://mp.weixin.qq.com/s 开头的公众号文章原文链接，不要任何解释或多余文字。";

// 面试后机会评分：6 维人工自评，权重合计 100。前端求和并给出结论分桶（与面试前 JD 评分 FitScore 解耦）。
const OPPORTUNITY_DIMENSIONS = [
  { key: "现金流", weight: 25 },
  { key: "岗位匹配", weight: 20 },
  { key: "业务闭环", weight: 20 },
  { key: "团队资源", weight: 15 },
  { key: "作息风险", weight: 10 },
  { key: "成长价值", weight: 10 }
] as const;

const INTERVIEW_ROUNDS = ["一面", "二面", "三面", "HR面", "复试", "终面"];

function sumOpportunity(details: Record<string, number>) {
  return OPPORTUNITY_DIMENSIONS.reduce((sum, dim) => sum + (Number(details?.[dim.key]) || 0), 0);
}

function concludeOpportunity(total: number) {
  if (total >= 80) return "重点推进";
  if (total >= 65) return "继续观察";
  if (total >= 50) return "保底";
  return "放弃";
}

// 面试后跟进话术的三种静态模板（来自方法论），一键填入复盘的 follow_up 后再按公司改。
const FOLLOWUP_TEMPLATES = [
  {
    label: "继续推进",
    text:
      "您好，感谢今天的沟通。通过这次交流，我对贵司岗位中独立站 SEO、网站维护和海外推广的工作内容更清楚了。\n\n" +
      "我这边比较匹配的是英文 B2B 内容、关键词规划、GSC/GA4 数据分析、TDK 和基础站内优化。如果后续有进一步面试或测试，我可以围绕贵司官网/产品方向准备更具体的优化思路。"
  },
  {
    label: "再确认重点",
    text:
      "您好，感谢今天沟通。我想再确认一下岗位重点：后续工作更偏官网/独立站 SEO 和 Google 推广，还是平台运营和日常维护为主？" +
      "另外团队里内容、设计、开发和外贸业务是否会有配合？"
  },
  {
    label: "婉拒",
    text:
      "您好，感谢今天沟通。结合岗位实际内容和我目前的求职方向，我这边更希望聚焦英文独立站 SEO、B2B 网站运营和 Google 数据分析方向。" +
      "这个岗位目前可能匹配度不是最高，先不继续推进了，也祝贵司招聘顺利。"
  }
] as const;

// 把一条复盘按"面试复盘模板"拼成 Markdown，配合 copyToClipboard 一键导出。
function interviewLogToMarkdown(log: InterviewLog, job?: Job | null) {
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

const PAGE_SIZE = 10;
const JOB_PAGE_SIZE = 20;
const USAGE_GUIDE_SEEN_KEY = "job-one-stop.usage-guide-seen.v1";
const ACTIVE_CHAT_THREAD_KEY = "job-one-stop.active-chat-thread.v1";
const GLOBAL_BUSY_KEYS = ["source-boss", "source-bebee", "upload", "wechat", "sprint", "manual", "profile", "export"] as const;

type ManualJob = {
  title: string;
  company_name: string;
  salary_text: string;
  city: string;
  area: string;
  skills: string;
  description: string;
};

type JobEditForm = ManualJob & {
  url: string;
  experience: string;
  degree: string;
  recruiter: string;
  published_at: string;
  recruitment_status: string;
};

type NoticeKind = "info" | "success" | "warning" | "error";

type Notice = {
  kind: NoticeKind;
  message: string;
  details?: string[];
};

function jobToEditForm(job: Job): JobEditForm {
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

function optionalText(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function jobEditPayload(form: JobEditForm): Partial<Job> {
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

function skippedItems(run?: SourceRun | null) {
  return run?.raw_config?.skipped ?? [];
}

function runDetailLines(run: SourceRun, fallback?: string) {
  const details: string[] = [];
  const report = run.raw_config;
  if (report?.urls_total != null) {
    details.push(`页面/链接解析：${report.urls_ok ?? 0}/${report.urls_total} 个成功。`);
  }
  if (report?.jobs != null) {
    details.push(`解析岗位：${report.jobs} 个。`);
  }
  for (const item of skippedItems(run)) {
    const reason = item.reason || "未说明原因";
    details.push(item.url ? `${item.url}：${reason}` : reason);
  }
  if (!details.length && fallback) details.push(fallback);
  return details;
}

function runHasNoJobs(run: SourceRun) {
  return run.fetched_count === 0 && run.created_count === 0 && run.updated_count === 0;
}

function jobSourceLabels(job: Job) {
  return Array.from(new Set([job.source, ...(job.source_links ?? []).map((link) => link.source)].filter(Boolean)));
}

function draftKindLabel(kind: string) {
  return draftKindLabels[kind] ?? kind;
}

const recruitmentStatusLabels: Record<string, string> = {
  active: "在招",
  closed: "已关闭",
  unknown: "未知"
};

const taskStatusLabels: Record<string, string> = {
  todo: "待办",
  done: "完成"
};

const applicationEventLabels: Record<string, string> = {
  applied: "已投递",
  reply: "已回复",
  interview_invite: "约面",
  rejected: "拒绝",
  offer: "Offer",
  withdrawn: "撤回"
};

function sortedTasks(tasks: FollowUpTask[]) {
  return [...tasks].sort((a, b) => {
    const doneDelta = Number(a.status === "done") - Number(b.status === "done");
    if (doneDelta) return doneDelta;
    const aDue = a.due_date ?? "9999-12-31";
    const bDue = b.due_date ?? "9999-12-31";
    if (aDue !== bDue) return aDue.localeCompare(bDue);
    return b.id - a.id;
  });
}

function rankedJobs(jobs: Job[]) {
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

function aiStatusLabel(status: AiStatus | null) {
  if (!status) return "读取中";
  if (!status.enabled_in_config) return "未启用";
  if (!status.api_key_configured) return "待配置";
  return status.available ? "可用" : "不可用";
}

function App() {
  const [activeNav, setActiveNav] = useState<(typeof navItems)[number]["id"]>("chat");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const { busy, runBusy } = useBusyState();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [runs, setRuns] = useState<SourceRun[]>([]);
  const [sources, setSources] = useState<JobSourceStatus[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [tasks, setTasks] = useState<FollowUpTask[]>([]);
  const [staleJobs, setStaleJobs] = useState<StaleJob[]>([]);
  const [interviews, setInterviews] = useState<InterviewLog[]>([]);
  const [funnel, setFunnel] = useState<FunnelAnalytics | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const jobDrawerRef = useRef<HTMLElement>(null);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [research, setResearch] = useState<ResearchItem[]>([]);
  const [prep, setPrep] = useState<InterviewPrep | null>(null);
  const [useAiPrep, setUseAiPrep] = useState(true);
  const [scores, setScores] = useState<FitScore[]>([]);
  const [jobEvents, setJobEvents] = useState<ApplicationEvent[]>([]);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [aiProbe, setAiProbe] = useState<AiProbeResult | null>(null);
  const [aiTesting, setAiTesting] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [jobSort, setJobSort] = useState<"default" | "score">("default");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [editJob, setEditJob] = useState<Job | null>(null);
  const [usageGuideOpen, setUsageGuideOpen] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadKeepTop, setUploadKeepTop] = useState(false);
  const [exportCenterOpen, setExportCenterOpen] = useState(false);
  const [wechatOpen, setWechatOpen] = useState(false);
  const [wechatText, setWechatText] = useState("");
  const [wechatResult, setWechatResult] = useState<SourceRun | null>(null);
  const [sprintBrief, setSprintBrief] = useState<SprintBrief | null>(null);
  const [manualJob, setManualJob] = useState<ManualJob>({
    title: "",
    company_name: "",
    salary_text: "",
    city: "",
    area: "",
    skills: "",
    description: ""
  });
  const [researchForm, setResearchForm] = useState({
    source_type: "manual_note",
    title: "",
    summary: "",
    source_url: "",
    sentiment: "neutral",
    confidence: 0.6
  });
  const loadAllRequestRef = useRef(0);
  const openJobRequestRef = useRef(0);

  function notify(kind: NoticeKind, message: string, details?: string[]) {
    setNotice({ kind, message, details: details?.filter(Boolean) });
  }

  function notifyRun(label: string, run: SourceRun, zeroFallback: string) {
    if (run.status !== "success") {
      notify("error", `${label}失败`, runDetailLines(run, run.error || "请检查采集配置和后端日志。"));
      return;
    }
    if (runHasNoJobs(run)) {
      notify(
        "warning",
        `${label}完成，但没有采集到可用岗位。`,
        runDetailLines(run, zeroFallback)
      );
      return;
    }
    const skipped = skippedItems(run).length;
    notify(
      "success",
      `${label}完成：抓到 ${run.fetched_count} 条，新增 ${run.created_count} / 更新 ${run.updated_count} 个岗位。`,
      skipped ? [`另有 ${skipped} 个页面/链接被跳过，可查看最近采集或弹窗详情。`] : undefined
    );
  }

  async function reload(keys?: string[]) {
    const requestId = ++loadAllRequestRef.current;
    const tasks: Array<{ key: string; label: string; run: () => Promise<unknown>; apply: (value: unknown) => void }> = [
      { key: "jobs", label: "岗位", run: () => api<Job[]>("/api/jobs"), apply: (value) => setJobs(value as Job[]) },
      { key: "companies", label: "公司", run: () => api<Company[]>("/api/companies"), apply: (value) => setCompanies(value as Company[]) },
      { key: "runs", label: "采集记录", run: () => api<SourceRun[]>("/api/collect/runs"), apply: (value) => setRuns(value as SourceRun[]) },
      { key: "sources", label: "来源状态", run: () => api<JobSourceStatus[]>("/api/sources"), apply: (value) => setSources(value as JobSourceStatus[]) },
      { key: "drafts", label: "草稿", run: () => api<Draft[]>("/api/drafts"), apply: (value) => setDrafts(value as Draft[]) },
      { key: "tasks", label: "待办", run: () => api<FollowUpTask[]>("/api/follow-ups"), apply: (value) => setTasks(value as FollowUpTask[]) },
      { key: "stale", label: "需跟进", run: () => api<StaleJob[]>("/api/follow-ups/stale"), apply: (value) => setStaleJobs(value as StaleJob[]) },
      { key: "interviews", label: "面试复盘", run: () => api<InterviewLog[]>("/api/interviews"), apply: (value) => setInterviews(value as InterviewLog[]) },
      { key: "funnel", label: "求职漏斗", run: () => api<FunnelAnalytics>("/api/analytics/funnel"), apply: (value) => setFunnel(value as FunnelAnalytics) },
      { key: "profile", label: "个人画像", run: () => api<UserProfile>("/api/profile"), apply: (value) => setProfile(value as UserProfile) },
      { key: "ai", label: "AI 状态", run: () => api<AiStatus>("/api/ai/status"), apply: (value) => setAiStatus(value as AiStatus) }
    ];
    const active = keys ? tasks.filter((task) => keys.includes(task.key)) : tasks;
    const results = await Promise.allSettled(active.map((task) => task.run()));
    if (requestId !== loadAllRequestRef.current) return;
    const failed = results
      .map((result, index) => {
        if (result.status === "fulfilled") {
          active[index].apply(result.value);
          return null;
        }
        return active[index].label;
      })
      .filter((label): label is string => Boolean(label));
    if (failed.length) {
      notify("warning", `部分数据加载失败：${failed.join("、")}。其余内容仍可使用，可稍后刷新重试。`);
    }
  }

  // 全量刷新(首屏 / 采集类动作);抽屉内动作改用 reload([...]) 只刷受影响切片,避免整表闪烁。
  async function loadAll() {
    return reload();
  }

  function closeJobDrawer() {
    openJobRequestRef.current += 1;
    setSelectedJob(null);
    setJobEvents([]);
  }

  function showJobs(nextStatus = "all") {
    closeJobDrawer();
    setActiveNav("jobs");
    setStatus(nextStatus);
    setSourceFilter("all");
  }

  function showNav(nextNav: (typeof navItems)[number]["id"]) {
    closeJobDrawer();
    setActiveNav(nextNav);
  }

  function showScoreQueue() {
    closeJobDrawer();
    setActiveNav("jobs");
    setStatus("all");
    setSourceFilter("all");
    setJobSort("score");
  }

  useEffect(() => {
    loadAll().catch((err) => notify("error", errorMessage(err, "加载数据失败")));
  }, []);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(USAGE_GUIDE_SEEN_KEY) === "true") return;
      window.localStorage.setItem(USAGE_GUIDE_SEEN_KEY, "true");
      setUsageGuideOpen(true);
    } catch {
      setUsageGuideOpen(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedJob) return;
    const fresh = jobs.find((job) => job.id === selectedJob.id);
    if (fresh) setSelectedJob(fresh);
  }, [jobs, selectedJob?.id]);

  // 抽屉打开时:←/k 上一个、→/j 下一个(复用 goAdjacent)。输入框聚焦或有弹窗盖在上面时让位,不翻页。
  // Esc 关闭内联弹窗(组件式弹窗在各自组件里调 useEscapeClose);栈式管理,Esc 只关最上层。
  useEscapeClose(manualOpen, () => setManualOpen(false));
  useEscapeClose(uploadOpen, () => setUploadOpen(false));
  useEscapeClose(wechatOpen, () => setWechatOpen(false));

  async function openJob(job: Job) {
    const requestId = ++openJobRequestRef.current;
    setSelectedJob(job);
    setSelectedCompany(null);
    setPrep(null);
    setScores([]);
    setResearch([]);
    setJobEvents([]);
    // 公司 / 评分 / 准备并发拉取：减少串行往返，单个失败不拖垮其余区块。
    const [companyResult, scoresResult, prepResult, eventsResult] = await Promise.allSettled([
      job.company_id
        ? api<Company & { research_items: ResearchItem[] }>(`/api/companies/${job.company_id}`)
        : Promise.resolve(null),
      api<FitScore[]>(`/api/jobs/${job.id}/score`),
      api<InterviewPrep | null>(`/api/jobs/${job.id}/prep`),
      api<ApplicationEvent[]>(`/api/jobs/${job.id}/events`)
    ]);
    if (requestId !== openJobRequestRef.current) return;
    if (companyResult.status === "fulfilled" && companyResult.value) {
      setSelectedCompany(companyResult.value);
      setResearch(companyResult.value.research_items ?? []);
    }
    if (scoresResult.status === "fulfilled") setScores(scoresResult.value);
    if (prepResult.status === "fulfilled") setPrep(prepResult.value);
    if (eventsResult.status === "fulfilled") setJobEvents(eventsResult.value);
  }

  const filteredJobs = useMemo(() => {
    const q = search.trim().toLowerCase();
    const result = jobs.filter((job) => {
      const sourceLabels = jobSourceLabels(job);
      const matchesSearch = !q || [job.title, job.company_name, job.skills, job.area, ...sourceLabels].filter(Boolean).join(" ").toLowerCase().includes(q);
      const matchesStatus = status === "all" || job.status === status;
      const matchesSource = sourceFilter === "all" || sourceLabels.includes(sourceFilter);
      return matchesSearch && matchesStatus && matchesSource;
    });
    return jobSort === "score" ? rankedJobs(result) : result;
  }, [jobs, search, status, sourceFilter, jobSort]);

  // 抽屉「上一个/下一个」：在当前筛选+排序后的列表里按位置翻卡，顺序与岗位池所见一致。
  const selectedIndex = selectedJob ? filteredJobs.findIndex((job) => job.id === selectedJob.id) : -1;
  function goAdjacent(delta: number) {
    if (selectedIndex < 0) return;
    const next = filteredJobs[selectedIndex + delta];
    if (next) openJob(next);
  }

  // 抽屉打开时:←/k 上一个、→/j 下一个(复用 goAdjacent)。输入框聚焦或有弹窗盖在上面时让位,不翻页。
  useEffect(() => {
    if (!selectedJob) return;

    function onKeyDown(event: KeyboardEvent) {
      if (isTypingElement(document.activeElement)) return;
      if (document.querySelector(".modal-backdrop")) return;
      if (event.key === "ArrowLeft" || event.key === "k") {
        event.preventDefault();
        goAdjacent(-1);
      } else if (event.key === "ArrowRight" || event.key === "j") {
        event.preventDefault();
        goAdjacent(1);
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [selectedJob, filteredJobs]);

  const jobSources = useMemo(() => Array.from(new Set(jobs.flatMap(jobSourceLabels))).sort(), [jobs]);
  const latestRun = runs[0];
  const latestSkipped = skippedItems(latestRun).length;
  const toolbarBusy = hasAnyBusy(busy, [...GLOBAL_BUSY_KEYS]);

  const metrics = useMemo(() => {
    return {
      total: jobs.length,
      fit: jobs.filter((job) => job.status === "fit").length,
      research: jobs.filter((job) => job.status === "researching").length,
      drafts: drafts.length
    };
  }, [drafts.length, jobs]);

  async function collectSource(sourceKey: string, label: string, zeroFallback: string) {
    if (hasAnyBusy(busy, [...GLOBAL_BUSY_KEYS])) return;
    const sourceInfo = sources.find((item) => item.key === sourceKey);
    if (sourceInfo?.status === "host_import_required") {
      const scriptName = sourceInfo.config.host_collection?.script ?? (sourceKey === "zhilian" ? "tools\\host_collect_zhilian.bat" : "tools\\host_collect_boss.bat");
      notify("warning", `${sourceInfo.label} 需要在宿主机采集后导入。`, [
        `保持 start_app.bat 启动的服务运行，然后双击 ${scriptName}。`,
        "主服务会接收生成的 CSV；如 PATH 找不到 OpenCLI，可在脚本后追加 --opencli <path>。"
      ]);
      return;
    }
    await runBusy(`source-${sourceKey}`, async () => {
      notify("info", `正在运行${label}…`);
      try {
        const run = await api<SourceRun>(`/api/sources/${sourceKey}/collect`, { method: "POST" });
        notifyRun(label, run, zeroFallback);
        await loadAll();
      } catch (err) {
        notify("error", errorMessage(err, `${label}失败`));
      }
    });
  }

  async function runBossCollection() {
    await collectSource("boss", "BOSS 采集", "未读取到岗位。请确认 OpenCLI 登录态、关键词、城市和命令配置。");
  }

  async function uploadFile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const fileInput = formElement.elements.namedItem("file") as HTMLInputElement | null;
    const files = Array.from(fileInput?.files ?? []);
    if (!files.length) return;
    const form = new FormData();
    files.forEach((file) => form.append("file", file));
    const params = new URLSearchParams({ source: "导入文件" });
    if (uploadKeepTop) params.set("keep_top_scored", "20");
    const fileLabel = files.length === 1 ? files[0].name : `${files.length} 个文件`;
    await runBusy("upload", async () => {
      notify("info", `正在导入 ${fileLabel}…`);
      try {
        const result = await api<{ fetched: number; created: number; updated: number; scored?: number; kept?: number; deleted?: number }>(`/api/jobs/import?${params.toString()}`, { method: "POST", body: form });
        const pruneText =
          result.deleted != null
            ? `评分 ${result.scored ?? 0} 个，保留 ${result.kept ?? 0} / 删除 ${result.deleted} 个。`
            : "";
        const message = `导入完成：读取 ${result.fetched} 条，新增 ${result.created} / 更新 ${result.updated} 个岗位。${pruneText}`;
        notify(result.fetched ? "success" : "warning", result.fetched ? message : "导入完成，但文件里没有识别到岗位。");
        setUploadOpen(false);
        await loadAll();
      } catch (err) {
        notify("error", errorMessage(err, "导入失败"));
      } finally {
        formElement.reset();
      }
    });
  }

  async function collectBeBee() {
    await collectSource("bebee", "beBee 采集", "当前 URL 可能不含 JobPosting/岗位卡片，或岗位由 JS 接口渲染。请更换 URL，或提供页面 HTML/JSON 样例。");
  }

  async function collectWeChat(event: FormEvent) {
    event.preventDefault();
    if (!wechatText.trim()) return;
    await runBusy("wechat", async () => {
      notify("info", "正在抓取公众号文章并导入岗位…");
      setWechatResult(null);
      try {
        const run = await api<SourceRun>("/api/collect/wechat", { method: "POST", ...jsonBody({ text: wechatText }) });
        setWechatResult(run);
        if (run.status === "success") {
          if (run.fetched_count > 0) setWechatText("");
          notifyRun("公众号导入", run, "未拆出岗位。可查看跳过原因；被风控或图片型文章可改为手动粘正文。");
        } else {
          notify("error", "公众号导入失败", runDetailLines(run, run.error || "请检查链接或改为手动粘正文。"));
        }
        await loadAll();
      } catch (err) {
        notify("error", errorMessage(err, "公众号采集失败"));
      }
    });
  }

  async function testAiConnection() {
    if (aiTesting) return;
    setAiTesting(true);
    setAiProbe(null);
    try {
      const result = await api<AiProbeResult>("/api/ai/test", { method: "POST" });
      setAiProbe(result);
    } catch (err) {
      setAiProbe({ ok: false, stage: "call", reason: errorMessage(err, "测试请求失败"), model: aiStatus?.model ?? "" });
    } finally {
      setAiTesting(false);
    }
  }

  async function createSprintBrief() {
    if (hasAnyBusy(busy, [...GLOBAL_BUSY_KEYS])) return;
    closeJobDrawer();
    await runBusy("sprint", async () => {
      notify("info", "正在生成今日求职冲刺包…");
      try {
        const brief = await api<SprintBrief>("/api/sprint/brief", { method: "POST" });
        setSprintBrief(brief);
        notify(
          brief.top_jobs.length ? "success" : "warning",
          brief.top_jobs.length
            ? `冲刺包已生成：Top ${brief.top_jobs.length} 岗位，准备 ${brief.prepared.length} 个，新增 ${brief.tasks_created.length} 个待办。`
            : "冲刺包已生成，但岗位池为空。",
          brief.top_jobs.length ? undefined : ["先采集或导入岗位后，再生成冲刺包会更有行动价值。"]
        );
        await loadAll();
      } catch (err) {
        notify("error", errorMessage(err, "冲刺包生成失败"));
      }
    });
  }

  async function createManualJob(event: FormEvent) {
    event.preventDefault();
    await runBusy("manual", async () => {
      notify("info", "正在保存新增岗位…");
      try {
        const created = await api<Job>("/api/jobs", { method: "POST", ...jsonBody(manualJob) });
        setManualOpen(false);
        setManualJob({ title: "", company_name: "", salary_text: "", city: "", area: "", skills: "", description: "" });
        notify("success", `岗位已新增：${created.company_name} · ${created.title}`);
        await loadAll();
      } catch (err) {
        notify("error", errorMessage(err, "新增失败"));
      }
    });
  }

  async function patchJob(job: Job, updates: Partial<Job>) {
    try {
      const updated = await api<Job>(`/api/jobs/${job.id}`, { method: "PATCH", ...jsonBody(updates) });
      setJobs((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      if (selectedJob?.id === updated.id) setSelectedJob(updated);
    } catch (err) {
      notify("error", errorMessage(err, "岗位更新失败"));
    }
  }

  async function saveJobEdit(job: Job, form: JobEditForm) {
    if (!form.title.trim() || !form.company_name.trim()) {
      notify("warning", "岗位和公司不能为空。");
      return;
    }
    await runBusy("edit-job", async () => {
      try {
        const updated = await api<Job>(`/api/jobs/${job.id}`, { method: "PATCH", ...jsonBody(jobEditPayload(form)) });
        setJobs((items) => items.map((item) => (item.id === updated.id ? updated : item)));
        setEditJob(null);
        notify("success", `岗位已更新：${updated.company_name} · ${updated.title}`);
        await openJob(updated);
      } catch (err) {
        notify("error", errorMessage(err, "岗位保存失败"));
      }
    });
  }

  async function bulkPatchJobs(ids: number[], updates: Pick<Partial<Job>, "status" | "favorite">) {
    if (!ids.length) return;
    await runBusy("bulk", async () => {
      try {
        const result = await api<JobBulkUpdateResult>("/api/jobs/bulk", { method: "PATCH", ...jsonBody({ ids, ...updates }) });
        const updatedById = new Map(result.jobs.map((job) => [job.id, job]));
        setJobs((items) => items.map((item) => updatedById.get(item.id) ?? item));
        if (selectedJob && updatedById.has(selectedJob.id)) {
          setSelectedJob(updatedById.get(selectedJob.id) ?? selectedJob);
        }
        notify(result.updated ? "success" : "warning", result.updated ? `已批量更新 ${result.updated} 个岗位。` : "没有匹配到可更新的岗位。");
      } catch (err) {
        notify("error", errorMessage(err, "批量更新失败"));
      }
    });
  }

  async function createScore() {
    if (!selectedJob) return;
    await runBusy("score", async () => {
      notify("info", "正在重新计算匹配评分…");
      try {
        const score = await api<FitScore>(`/api/jobs/${selectedJob.id}/score`, { method: "POST" });
        setScores((items) => [score, ...items]);
        await reload(["jobs", "funnel"]);
        notify("success", `匹配评分已更新：${score.total} 分。`);
      } catch (err) {
        notify("error", errorMessage(err, "重新评分失败"));
      }
    });
  }

  async function createPrep() {
    if (!selectedJob) return;
    await runBusy("prep", async () => {
      notify("info", "正在生成面试准备包…");
      try {
        const newPrep = await api<InterviewPrep>(`/api/jobs/${selectedJob.id}/prep?ai=${useAiPrep}`, { method: "POST" });
        setPrep(newPrep);
        await reload(["drafts"]);
        notify("success", "面试准备包已生成。");
      } catch (err) {
        notify("error", errorMessage(err, "面试准备包生成失败"));
      }
    });
  }

  async function addResearch(event: FormEvent) {
    event.preventDefault();
    if (!selectedJob?.company_id) return;
    await runBusy("research", async () => {
      notify("info", "正在保存公司证据…");
      try {
        const item = await api<ResearchItem>(`/api/companies/${selectedJob.company_id}/research`, {
          method: "POST",
          ...jsonBody(researchForm)
        });
        setResearch((items) => [item, ...items]);
        setResearchForm({ source_type: "manual_note", title: "", summary: "", source_url: "", sentiment: "neutral", confidence: 0.6 });
        notify("success", "公司证据已保存。");
      } catch (err) {
        notify("error", errorMessage(err, "公司证据保存失败"));
      }
    });
  }

  async function updateProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!profile) return;
    const form = new FormData(event.currentTarget);
    const payload = {
      target_titles: String(form.get("target_titles") ?? ""),
      target_cities: String(form.get("target_cities") ?? ""),
      salary_min_k: Number(form.get("salary_min_k") ?? 0),
      salary_max_k: Number(form.get("salary_max_k") ?? 0),
      skills: String(form.get("skills") ?? ""),
      strengths: String(form.get("strengths") ?? ""),
      work_experience: String(form.get("work_experience") ?? ""),
      dealbreakers: String(form.get("dealbreakers") ?? ""),
      commute_preferences: String(form.get("commute_preferences") ?? "")
    };
    await runBusy("profile", async () => {
      notify("info", "正在保存个人画像…");
      try {
        const updated = await api<UserProfile>("/api/profile", { method: "PUT", ...jsonBody(payload) });
        setProfile(updated);
        notify("success", "画像已保存，后续评分会按新画像计算。", ["历史评分不会自动重算；可打开岗位重新评分，或直接生成今日求职冲刺包。"]);
      } catch (err) {
        notify("error", errorMessage(err, "画像保存失败"));
      }
    });
  }

  async function addTask(title: string, jobId?: number, dueDate?: string) {
    await runBusy("task", async () => {
      notify("info", "正在新增待办…");
      try {
        const task = await api<FollowUpTask>("/api/follow-ups", {
          method: "POST",
          ...jsonBody({ title, job_id: jobId, due_date: dueDate || null })
        });
        setTasks((items) => [task, ...items]);
        notify("success", "待办已新增。");
      } catch (err) {
        notify("error", errorMessage(err, "待办新增失败"));
      }
    });
  }

  async function updateTask(task: FollowUpTask, updates: Partial<FollowUpTask>) {
    await runBusy(`task-${task.id}`, async () => {
      try {
        const updated = await api<FollowUpTask>(`/api/follow-ups/${task.id}`, { method: "PATCH", ...jsonBody(updates) });
        setTasks((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      } catch (err) {
        notify("error", errorMessage(err, "待办更新失败"));
      }
    });
  }

  async function deleteTask(task: FollowUpTask) {
    await runBusy(`task-${task.id}`, async () => {
      try {
        await api<{ deleted: boolean; id: number }>(`/api/follow-ups/${task.id}`, { method: "DELETE" });
        setTasks((items) => items.filter((item) => item.id !== task.id));
        notify("success", "待办已删除。");
      } catch (err) {
        notify("error", errorMessage(err, "待办删除失败"));
      }
    });
  }

  async function addInterview(payload: Partial<InterviewLog>) {
    if (!selectedJob) return;
    await runBusy("interview", async () => {
      notify("info", "正在保存面试复盘…");
      try {
        await api<InterviewLog>(`/api/jobs/${selectedJob.id}/interviews`, { method: "POST", ...jsonBody(payload) });
        await reload(["interviews"]);
        notify("success", "面试复盘已保存。");
      } catch (err) {
        notify("error", errorMessage(err, "面试复盘保存失败"));
      }
    });
  }

  async function deleteInterview(log: InterviewLog) {
    await runBusy(`interview-${log.id}`, async () => {
      try {
        await api<{ deleted: boolean; id: number }>(`/api/interviews/${log.id}`, { method: "DELETE" });
        setInterviews((items) => items.filter((item) => item.id !== log.id));
        notify("success", "面试复盘已删除。");
      } catch (err) {
        notify("error", errorMessage(err, "面试复盘删除失败"));
      }
    });
  }

  async function copyInterviewMarkdown(log: InterviewLog) {
    const job = jobs.find((item) => item.id === log.job_id) ?? (selectedJob?.id === log.job_id ? selectedJob : null);
    const ok = await copyToClipboard(interviewLogToMarkdown(log, job));
    notify(ok ? "success" : "warning", ok ? "已复制复盘 Markdown。" : "复制失败，请手动选择文本复制。");
  }

  async function exportFile(path: string, fallbackName: string, successMessage: string) {
    await runBusy("export", async () => {
      try {
        const filename = await downloadApiFile(path, fallbackName);
        notify("success", `${successMessage}：${filename}`);
      } catch (err) {
        notify("error", errorMessage(err, "导出失败"));
      }
    });
  }

  async function addJobEvent(job: Job, payload: { event_type: string; event_date: string; channel?: string; note?: string }) {
    await runBusy("job-event", async () => {
      try {
        const created = await api<ApplicationEvent>(`/api/jobs/${job.id}/events`, {
          method: "POST",
          ...jsonBody({
            event_type: payload.event_type,
            event_date: payload.event_date,
            channel: payload.channel || null,
            note: payload.note || "",
          })
        });
        setJobEvents((items) => [created, ...items]);
        await reload(["jobs", "funnel"]);
        notify("success", `已记录事件：${applicationEventLabels[created.event_type] ?? created.event_type}`);
      } catch (err) {
        notify("error", errorMessage(err, "事件记录失败"));
      }
    });
  }

  async function deleteJobEvent(event: ApplicationEvent) {
    await runBusy(`job-event-${event.id}`, async () => {
      try {
        await api<{ deleted: boolean; id: number }>(`/api/events/${event.id}`, { method: "DELETE" });
        setJobEvents((items) => items.filter((item) => item.id !== event.id));
        await reload(["jobs", "funnel"]);
        notify("success", "事件已删除。");
      } catch (err) {
        notify("error", errorMessage(err, "事件删除失败"));
      }
    });
  }

  return (
    <div className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}${activeNav === "chat" ? " chat-active" : ""}`}>
      <aside className="sidebar" aria-label="主导航">
        <div className="brand">
          <div className="brand-mark">J1</div>
          <div className="brand-copy">
            <strong>job-one-stop</strong>
            <span>本地求职助手</span>
          </div>
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarCollapsed((value) => !value)}
            title={sidebarCollapsed ? "展开侧边栏" : "折叠侧边栏"}
            aria-label={sidebarCollapsed ? "展开侧边栏" : "折叠侧边栏"}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
        </div>
        <nav data-tour="nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={activeNav === item.id ? "nav-item active" : "nav-item"}
                onClick={() => {
                  setActiveNav(item.id);
                  closeJobDrawer();
                }}
                title={item.label}
              >
                <Icon size={18} />
                <span className="nav-label">{item.label}</span>
              </button>
            );
          })}
        </nav>
        {activeNav !== "chat" && (
          <div className="run-strip">
            <span>{latestRun ? `最近采集 · ${latestRun.source}` : "最近采集"}</span>
            <strong>{latestRun?.status ?? "未运行"}</strong>
            <small>
              {latestRun
                ? `${latestRun.fetched_count} 抓取 / ${latestRun.created_count} 新增 / ${latestRun.updated_count} 更新${latestSkipped ? ` / ${latestSkipped} 跳过` : ""}`
                : "等待首次采集"}
            </small>
          </div>
        )}
        <div className="run-strip ai-status">
          <span>AI 配置</span>
          <strong>{aiStatusLabel(aiStatus)}</strong>
          <small>
            {aiStatus
              ? `${aiStatus.model} · Key ${aiStatus.api_key_configured ? "已配置" : "未配置"} · Base URL ${aiStatus.base_url_configured ? "已配置" : "默认"}`
              : "正在读取"}
          </small>
          <button
            className="small-action ai-test-button"
            type="button"
            onClick={testAiConnection}
            disabled={aiTesting || !aiStatus?.enabled_in_config}
          >
            {aiTesting ? <Loader2 className="spin" size={13} /> : <RefreshCw size={13} />}
            {aiTesting ? "测试中…" : "测试连接"}
          </button>
          {aiProbe && (
            <div className={`ai-probe-result ${aiProbe.ok ? "ok" : "fail"}`} aria-live="polite">
              {aiProbe.ok ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
              <span>
                {aiProbe.ok
                  ? `连接成功 · ${aiProbe.model}${aiProbe.latency_ms != null ? ` · ${aiProbe.latency_ms}ms` : ""}`
                  : aiProbe.reason}
              </span>
            </div>
          )}
        </div>
      </aside>

      <main className={activeNav === "chat" ? "workspace chat-workspace" : "workspace"}>
        {activeNav !== "chat" && <header className="topbar">
          <div>
            <h1>{navItems.find((item) => item.id === activeNav)?.label}</h1>
            <p>岗位发现、公司证据、匹配评分、准备材料都留在本机。</p>
          </div>
          <div className="toolbar-actions">
            <button data-tour="guide" className="icon-button" title="打开使用引导" onClick={() => setTourOpen(true)}>
              <Info size={18} />
            </button>
            <button className="icon-button" title="导出中心" onClick={() => setExportCenterOpen(true)} disabled={hasBusy(busy, "export")}>
              {hasBusy(busy, "export") ? <Loader2 size={18} className="spin" /> : <Download size={18} />}
            </button>
            <button data-tour="collect" className="icon-button" title="运行 BOSS 采集" onClick={runBossCollection} disabled={toolbarBusy}>
              {hasBusy(busy, "source-boss") ? <Loader2 size={18} className="spin" /> : <RefreshCw size={18} />}
            </button>
            <button className="icon-button" title="采集 beBee(按 config.yaml 角色页)" onClick={collectBeBee} disabled={toolbarBusy}>
              {hasBusy(busy, "source-bebee") ? <Loader2 size={18} className="spin" /> : <Globe size={18} />}
            </button>
            <button className="icon-button" title="导入 CSV/XLSX" onClick={() => setUploadOpen(true)} disabled={toolbarBusy}>
              {hasBusy(busy, "upload") ? <Loader2 size={18} className="spin" /> : <Upload size={18} />}
            </button>
            <button
              data-tour="wechat"
              className="icon-button"
              title="公众号 / 元宝导入"
              onClick={() => {
                closeJobDrawer();
                setWechatResult(null);
                setWechatOpen(true);
              }}
              disabled={toolbarBusy}
            >
              <MessageSquareText size={18} />
            </button>
            <button data-tour="sprint" className="icon-button" title="生成今日求职冲刺包" onClick={createSprintBrief} disabled={toolbarBusy}>
              {hasBusy(busy, "sprint") ? <Loader2 size={18} className="spin" /> : <ClipboardList size={18} />}
            </button>
            <button
              data-tour="manual"
              className="primary-action"
              onClick={() => {
                closeJobDrawer();
                setManualOpen(true);
              }}
              disabled={toolbarBusy}
            >
              <Plus size={18} />
              新增岗位
            </button>
          </div>
        </header>}

        {notice && <NoticeBanner notice={notice} onClose={() => setNotice(null)} />}

        {activeNav !== "config" && activeNav !== "chat" && (
          <StatBar
            metrics={metrics}
            funnel={funnel}
            onShowJobs={showJobs}
            onShowScoreQueue={showScoreQueue}
            onShowTasks={() => showNav("tasks")}
            onShowPrep={() => showNav("prep")}
          />
        )}

        <section className="workspace-content">
          {activeNav === "chat" && <ChatView jobs={jobs} onOpenJob={openJob} aiAvailable={Boolean(aiStatus?.available)} />}
          {activeNav === "jobs" && (
            <JobsView
              jobs={filteredJobs}
              search={search}
              status={status}
              source={sourceFilter}
              sources={jobSources}
              sort={jobSort}
              onSearch={setSearch}
              onStatus={setStatus}
              onSource={setSourceFilter}
              onSort={setJobSort}
              onOpen={openJob}
              onPatch={patchJob}
              onBulkPatch={bulkPatchJobs}
              busy={busy}
              onExport={() => exportFile(`/api/exports/jobs?format=csv&status=${status === "all" ? "" : status}&source=${sourceFilter === "all" ? "" : sourceFilter}`, "jobs.csv", "岗位池已导出")}
            />
          )}
          {activeNav === "companies" && <CompaniesView companies={companies} jobs={jobs} onOpenJob={openJob} />}
          {activeNav === "prep" && <PrepView jobs={jobs} drafts={drafts} onOpen={openJob} />}
          {activeNav === "interviews" && (
            <InterviewsView
              interviews={interviews}
              jobs={jobs}
              busy={busy}
              onOpenJob={openJob}
              onDelete={deleteInterview}
              onCopyMarkdown={copyInterviewMarkdown}
            />
          )}
          {activeNav === "tasks" && (
            <TasksView
              tasks={tasks}
              staleJobs={staleJobs}
              jobs={jobs}
              busy={busy}
              onAddTask={addTask}
              onUpdateTask={updateTask}
              onDeleteTask={deleteTask}
              onOpenJob={openJob}
            />
          )}
          {activeNav === "config" && (
            <ConfigView
              sources={sources}
              runs={runs}
              busy={busy}
              profile={profile}
              onNotify={notify}
              onAiStatus={setAiStatus}
              onCollectSource={collectSource}
              onUpdateProfile={updateProfile}
            />
          )}
        </section>
      </main>

      {selectedJob && (
        <JobDrawer
          job={selectedJob}
          company={selectedCompany}
          research={research}
          scores={scores}
          prep={prep}
          events={jobEvents}
          interviews={interviews.filter((log) => log.job_id === selectedJob.id)}
          drawerRef={jobDrawerRef}
          researchForm={researchForm}
          busy={busy}
          onClose={closeJobDrawer}
          onEdit={() => setEditJob(selectedJob)}
          onPatch={patchJob}
          onResearchForm={setResearchForm}
          onAddResearch={addResearch}
          onScore={createScore}
          onPrep={createPrep}
          aiAvailable={Boolean(aiStatus?.available)}
          useAiPrep={useAiPrep}
          onUseAiPrepChange={setUseAiPrep}
          onTask={() => addTask(`待办 ${selectedJob.company_name} - ${selectedJob.title}`, selectedJob.id)}
          onAddEvent={(payload) => addJobEvent(selectedJob, payload)}
          onDeleteEvent={deleteJobEvent}
          onAddInterview={addInterview}
          onDeleteInterview={deleteInterview}
          onCopyInterviewMarkdown={copyInterviewMarkdown}
          onPrev={() => goAdjacent(-1)}
          onNext={() => goAdjacent(1)}
          hasPrev={selectedIndex > 0}
          hasNext={selectedIndex >= 0 && selectedIndex < filteredJobs.length - 1}
          position={selectedIndex >= 0 ? `${selectedIndex + 1} / ${filteredJobs.length}` : ""}
        />
      )}

      {editJob && (
        <JobEditModal
          job={editJob}
          busy={busy}
          onClose={() => setEditJob(null)}
          onSave={(form) => saveJobEdit(editJob, form)}
        />
      )}

      {manualOpen && (
        <div className="modal-backdrop">
          <form className="modal" onSubmit={createManualJob}>
            <div className="modal-head">
              <h2>新增岗位</h2>
              <button type="button" className="icon-button" onClick={() => setManualOpen(false)} title="关闭">
                <X size={18} />
              </button>
            </div>
            <div className="form-grid">
              <label>
                岗位
                <input value={manualJob.title} onChange={(event) => setManualJob({ ...manualJob, title: event.target.value })} required />
              </label>
              <label>
                公司
                <input value={manualJob.company_name} onChange={(event) => setManualJob({ ...manualJob, company_name: event.target.value })} required />
              </label>
              <label>
                薪资
                <input value={manualJob.salary_text} onChange={(event) => setManualJob({ ...manualJob, salary_text: event.target.value })} placeholder="8-12K" />
              </label>
              <label>
                城市
                <input value={manualJob.city} onChange={(event) => setManualJob({ ...manualJob, city: event.target.value })} />
              </label>
              <label>
                区域
                <input value={manualJob.area} onChange={(event) => setManualJob({ ...manualJob, area: event.target.value })} />
              </label>
              <label>
                技能
                <input value={manualJob.skills} onChange={(event) => setManualJob({ ...manualJob, skills: event.target.value })} />
              </label>
            </div>
            <label>
              JD 摘要
              <textarea value={manualJob.description} onChange={(event) => setManualJob({ ...manualJob, description: event.target.value })} />
            </label>
            <button className="primary-action" disabled={hasBusy(busy, "manual")}>
              <Plus size={18} />
              保存
            </button>
          </form>
        </div>
      )}

      {uploadOpen && (
        <div className="modal-backdrop">
          <form className="modal" onSubmit={uploadFile}>
            <div className="modal-head">
              <div>
                <h2>导入 CSV/XLSX</h2>
                <p className="muted">可直接导入完整文件，或只保留本次导入里评分最高的 20 项。</p>
              </div>
              <button type="button" className="icon-button" onClick={() => setUploadOpen(false)} title="关闭">
                <X size={18} />
              </button>
            </div>
            <label>
              文件（可多选）
              <input name="file" type="file" accept=".csv,.xlsx" multiple required disabled={hasBusy(busy, "upload")} />
            </label>
            <label className="switch-field">
              <input
                type="checkbox"
                checked={uploadKeepTop}
                onChange={(event) => setUploadKeepTop(event.target.checked)}
                disabled={hasBusy(busy, "upload")}
              />
              <span>导入后只保留本次评分最高 20 项，删除其余本次导入岗位</span>
            </label>
            <button className="primary-action" disabled={hasBusy(busy, "upload")}>
              {hasBusy(busy, "upload") ? <Loader2 size={18} className="spin" /> : <Upload size={18} />}
              {hasBusy(busy, "upload") ? "导入中…" : "开始导入"}
            </button>
          </form>
        </div>
      )}

      {wechatOpen && (
        <div className="modal-backdrop">
          <form className="modal" onSubmit={collectWeChat}>
            <div className="modal-head">
              <h2>公众号 / 元宝导入</h2>
              <button type="button" className="icon-button" onClick={() => setWechatOpen(false)} title="关闭">
                <X size={18} />
              </button>
            </div>
            <p className="muted">
              在腾讯元宝（网页版）用提示词问一次，把它给出的回答或 mp.weixin 链接整段粘贴到下方。
              系统会自动提取链接、抓取正文并拆出岗位；被风控拦截的文章可改为直接粘贴正文。
            </p>
            <button
              type="button"
              className="small-action"
              onClick={async () => {
                const ok = await copyToClipboard(YUANBAO_PROMPT);
                notify(ok ? "success" : "warning", ok ? "已复制元宝提示词。" : "复制失败，请手动选择文本复制。");
              }}
              title="复制后到元宝粘贴提问"
            >
              复制元宝提示词
            </button>
            <label>
              粘贴元宝回答 / 公众号文章链接
              <textarea
                value={wechatText}
                onChange={(event) => setWechatText(event.target.value)}
                rows={8}
                placeholder="例如：https://mp.weixin.qq.com/s/xxxx ，可一次粘贴多个，或直接粘贴元宝的整段回答"
                required
              />
            </label>
            <button className="primary-action" disabled={hasBusy(busy, "wechat")}>
              <MessageSquareText size={18} />
              {hasBusy(busy, "wechat") ? "采集中…" : "抓取并入库"}
            </button>

            {wechatResult && (
              <div className="wechat-result">
                <p>
                  本次：识别链接 <b>{wechatResult.raw_config?.input_links ?? "-"}</b>，
                  成功 <b>{wechatResult.raw_config?.urls_ok ?? "-"}</b> 篇，
                  新增 <b>{wechatResult.created_count}</b> / 更新 <b>{wechatResult.updated_count}</b> 个岗位。
                </p>
                {!!wechatResult.raw_config?.skipped?.length && (
                  <details>
                    <summary>{wechatResult.raw_config.skipped.length} 篇被跳过（可改为手动粘正文重试）</summary>
                    <ul>
                      {wechatResult.raw_config.skipped.map((item) => (
                        <li key={item.url}>
                          <a href={item.url} target="_blank" rel="noreferrer">
                            {item.url}
                          </a>
                          <span>{item.reason}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </form>
        </div>
      )}

      {sprintBrief && <SprintBriefModal brief={sprintBrief} onClose={() => setSprintBrief(null)} />}
      {exportCenterOpen && <ExportCenterModal onClose={() => setExportCenterOpen(false)} onExport={exportFile} busy={hasBusy(busy, "export")} />}
      {usageGuideOpen && (
        <UsageGuideModal
          onClose={() => setUsageGuideOpen(false)}
          onStartTour={() => {
            setUsageGuideOpen(false);
            setTourOpen(true);
          }}
        />
      )}
      {tourOpen && <Tour steps={TOUR_STEPS} onClose={() => setTourOpen(false)} />}
    </div>
  );
}

function UsageGuideModal({ onClose, onStartTour }: { onClose: () => void; onStartTour: () => void }) {
  useEscapeClose(true, onClose);
  return (
    <div className="modal-backdrop">
      <div className="modal usage-guide-modal" role="dialog" aria-modal="true" aria-labelledby="usage-guide-title">
        <div className="modal-head">
          <div>
            <h2 id="usage-guide-title">使用指南</h2>
            <p className="muted">先用决策聊天判断，再按“岗位池、调研、准备、待办”推进，每天用冲刺包收口。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="guide-grid">
          <article className="guide-card guide-card-primary">
            <span>每日闭环</span>
            <ol>
              <li>在匹配评分里校准个人画像、目标城市、薪资和排除项。</li>
              <li>通过宿主机采集、CSV、公众号或 beBee 补充真实岗位。</li>
              <li>打开高潜岗位，补公司证据、刷新评分并生成准备材料。</li>
              <li>生成今日求职冲刺包，把 Top 岗位转成待办。</li>
            </ol>
          </article>

          <article className="guide-card">
            <span>采集边界</span>
            <p>BOSS / 智联在宿主机运行 OpenCLI 后导入；公众号和 beBee 若返回 0 岗位，先看跳过原因，再补正文、HTML 或 Network JSON 样例。</p>
          </article>

          <article className="guide-card">
            <span>数据边界</span>
            <p>岗位、公司证据、评分和任务都保存在本机 SQLite；密钥只放环境变量或 .env。系统只生成材料，不自动投递、不自动发消息。</p>
          </article>
        </div>

        <div className="guide-actions">
          <button type="button" className="small-action" onClick={onClose}>
            稍后再说
          </button>
          <button type="button" className="primary-action" onClick={onStartTour}>
            <CheckCircle2 size={18} />
            开始引导
          </button>
        </div>
      </div>
    </div>
  );
}

function StatBar({
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

function ExportCenterModal({
  onClose,
  onExport,
  busy
}: {
  onClose: () => void;
  onExport: (path: string, fallbackName: string, successMessage: string) => Promise<void>;
  busy: boolean;
}) {
  useEscapeClose(true, onClose);
  const items = [
    { title: "岗位池", detail: "导出当前岗位、状态、分数、来源和链接（CSV，可用 Excel 打开）。", path: "/api/exports/jobs?format=csv", filename: "jobs.csv", message: "岗位池已导出" },
    { title: "完整归档", detail: "导出 JSON 备份，包含画像、岗位、评分、准备、待办、复盘和事件，可迁移留档。", path: "/api/exports/archive?format=json", filename: "archive.json", message: "完整归档已导出" }
  ];
  return (
    <div className="modal-backdrop">
      <div className="modal export-modal" role="dialog" aria-modal="true" aria-labelledby="export-center-title">
        <div className="modal-head">
          <div>
            <h2 id="export-center-title">导出中心</h2>
            <p className="muted">默认只导出本机数据，不上传云端，不包含环境变量和密钥。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="export-grid">
          {items.map((item) => (
            <article key={item.title} className="export-card">
              <div>
                <strong>{item.title}</strong>
                <p>{item.detail}</p>
              </div>
              <button
                type="button"
                className="small-action"
                onClick={() => onExport(item.path, item.filename, item.message)}
                disabled={busy}
              >
                <Download size={14} />
                导出
              </button>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

function SprintBriefModal({ brief, onClose }: { brief: SprintBrief; onClose: () => void }) {
  useEscapeClose(true, onClose);
  const [copied, setCopied] = useState(false);
  async function copyMarkdown() {
    const ok = await copyToClipboard(brief.markdown);
    setCopied(ok);
    if (ok) window.setTimeout(() => setCopied(false), 1200);
  }
  return (
    <div className="modal-backdrop">
      <div className="modal sprint-modal">
        <div className="modal-head">
          <div>
            <h2>今日求职冲刺包</h2>
            <p className="muted">
              Top {brief.top_jobs.length} 岗位，已准备 {brief.prepared.length} 个，新增 {brief.tasks_created.length} 个待办。
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="sprint-summary">
          {brief.top_jobs.slice(0, 5).map((job, index) => (
            <article key={job.id} className="sprint-job">
              <span>{index + 1}</span>
              <div>
                <strong>{job.company_name}</strong>
                <p>{job.title}</p>
              </div>
              <b className={scoreClass(job.latest_score.total)}>{job.latest_score.total}</b>
            </article>
          ))}
          {!brief.top_jobs.length && <p className="muted">暂无岗位。先采集或导入岗位后再生成冲刺包。</p>}
        </div>

        <label>
          可复制 Markdown
          <textarea className="sprint-markdown" readOnly value={brief.markdown} />
        </label>
        <div className="row-actions">
          <button type="button" className="primary-action" onClick={copyMarkdown}>
            {copied ? <CheckCircle2 size={18} /> : <ClipboardList size={18} />}
            {copied ? "已复制" : "复制 Markdown"}
          </button>
        </div>
      </div>
    </div>
  );
}

function NoticeBanner({ notice, onClose }: { notice: Notice; onClose: () => void }) {
  const Icon = {
    info: Info,
    success: CheckCircle2,
    warning: AlertTriangle,
    error: AlertCircle
  }[notice.kind];
  return (
    <div className={`notice-bar ${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
      <Icon size={18} />
      <div>
        <strong>{notice.message}</strong>
        {!!notice.details?.length && (
          <ul>
            {notice.details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" className="icon-button" onClick={onClose} title="关闭通知">
        <X size={16} />
      </button>
    </div>
  );
}

function PaginationControls({
  page,
  total,
  pageSize,
  onPage
}: {
  page: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(total, page * pageSize);
  const [pageInput, setPageInput] = useState(String(page));
  useEffect(() => {
    setPageInput(String(page));
  }, [page]);

  function commitPage() {
    const parsed = Number.parseInt(pageInput, 10);
    const next = Number.isFinite(parsed) ? Math.min(pageCount, Math.max(1, parsed)) : page;
    setPageInput(String(next));
    onPage(next);
  }

  return (
    <div className="pagination">
      <span>
        {start}-{end} / {total}
      </span>
      <button className="small-action" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1}>
        上一页
      </button>
      <span>
        {page} / {pageCount}
      </span>
      <label className="page-jump">
        跳至
        <input
          value={pageInput}
          type="number"
          min={1}
          max={pageCount}
          onChange={(event) => setPageInput(event.target.value)}
          onBlur={commitPage}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitPage();
            }
          }}
        />
      </label>
      <button className="small-action" onClick={() => onPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount}>
        下一页
      </button>
    </div>
  );
}

function ChatView({ jobs, onOpenJob, aiAvailable }: { jobs: Job[]; onOpenJob: (job: Job) => void | Promise<void>; aiAvailable: boolean }) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<number | null>(() => {
    try {
      const stored = window.localStorage.getItem(ACTIVE_CHAT_THREAD_KEY);
      return stored ? Number(stored) || null : null;
    } catch {
      return null;
    }
  });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [imageDataUrl, setImageDataUrl] = useState("");
  const [imageName, setImageName] = useState("");
  const [jobId, setJobId] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState<{ content: string; imageDataUrl: string; imageName: string } | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ChatContextPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // 阶段进度：结构化决策卡无法逐字流式，改为显式展示「检查规则 → 询问模型 → 整理结果」，让等待可见。
  const [stage, setStage] = useState(0);
  const stageTimers = useRef<number[]>([]);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const activeThread = threads.find((thread) => thread.id === activeThreadId) ?? null;
  const activeJob = activeThread?.job_id ? jobs.find((job) => job.id === activeThread.job_id) ?? null : null;

  async function loadThreads(preferredId?: number | null) {
    const loaded = await api<ChatThread[]>("/api/chat/threads");
    setThreads(loaded);
    const candidate = preferredId ?? activeThreadId;
    const nextId = candidate && loaded.some((thread) => thread.id === candidate) ? candidate : loaded[0]?.id ?? null;
    setActiveThreadId(nextId);
    return nextId;
  }

  async function loadMessages(threadId: number) {
    const detail = await api<ChatThreadDetail>(`/api/chat/threads/${threadId}`);
    setMessages(detail.messages);
    setThreads((items) => items.map((item) => (item.id === detail.thread.id ? detail.thread : item)));
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadThreads()
      .then((threadId) => (threadId && !cancelled ? loadMessages(threadId) : undefined))
      .catch((err) => !cancelled && setError(errorMessage(err, "聊天记录加载失败")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeThreadId || loading) return;
    try {
      window.localStorage.setItem(ACTIVE_CHAT_THREAD_KEY, String(activeThreadId));
    } catch {
      // Local storage is optional; SQLite remains the source of truth.
    }
    setError("");
    setPreview(null);
    loadMessages(activeThreadId).catch((err) => setError(errorMessage(err, "聊天记录加载失败")));
  }, [activeThreadId]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pendingMessage, sending]);

  useEffect(() => {
    setRenaming(false);
    setRenameDraft(activeThread?.title ?? "");
  }, [activeThreadId, activeThread?.title]);

  async function createThread(kind: "general" | "job") {
    setError("");
    if (kind === "job" && !jobId) {
      setError("请先选择一个岗位。");
      return;
    }
    try {
      const thread = await api<ChatThread>("/api/chat/threads", {
        method: "POST",
        ...jsonBody({ kind, job_id: kind === "job" ? Number(jobId) : null })
      });
      await loadThreads(thread.id);
      setActiveThreadId(thread.id);
      await loadMessages(thread.id);
      if (kind === "job") setJobId("");
    } catch (err) {
      setError(errorMessage(err, "创建聊天失败"));
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim() || (imageDataUrl ? "请分析这张截图。" : "");
    if (!activeThreadId || !content || sending) return;
    const sentImageDataUrl = imageDataUrl;
    const sentImageName = imageName;
    setSending(true);
    setError("");
    setDraft("");
    setImageDataUrl("");
    setImageName("");
    setPendingMessage({ content, imageDataUrl: sentImageDataUrl, imageName: sentImageName });
    // 阶段推进：规则先跑（立即），启用 AI 时约 0.4s 进入「询问模型」，兜底再显示「整理结果」。
    // 只是等待时的可见进度，不改变后端流程；请求返回时立刻清掉。
    setStage(0);
    stageTimers.current.forEach((id) => window.clearTimeout(id));
    stageTimers.current = aiAvailable
      ? [window.setTimeout(() => setStage(1), 400), window.setTimeout(() => setStage(2), 3500)]
      : [window.setTimeout(() => setStage(2), 250)];
    try {
      const reply = await api<ChatReply>(`/api/chat/threads/${activeThreadId}/messages`, {
        method: "POST",
        ...jsonBody({ content, image_data_url: sentImageDataUrl || null, image_name: sentImageName || null })
      });
      setMessages((items) => [...items, reply.user_message, reply.assistant_message]);
      await loadThreads(activeThreadId);
      setPreview(null);
    } catch (err) {
      setDraft(content);
      setImageDataUrl(sentImageDataUrl);
      setImageName(sentImageName);
      setError(errorMessage(err, "分析失败；消息可能已保存在本机，可刷新查看"));
    } finally {
      stageTimers.current.forEach((id) => window.clearTimeout(id));
      stageTimers.current = [];
      setStage(0);
      setPendingMessage(null);
      setSending(false);
    }
  }

  // 发送前预览：显示启用 AI 时这一线程会离开本机的固定上下文（决策规则/画像/看板 + 岗位事实 + 最近对话）。
  async function togglePreview() {
    if (preview) {
      setPreview(null);
      return;
    }
    if (!activeThreadId || previewLoading) return;
    setPreviewLoading(true);
    setError("");
    try {
      const result = await api<ChatContextPreview>(`/api/chat/threads/${activeThreadId}/context-preview`);
      setPreview(result);
    } catch (err) {
      setError(errorMessage(err, "预览加载失败"));
    } finally {
      setPreviewLoading(false);
    }
  }

  async function renameThread(event: FormEvent) {
    event.preventDefault();
    const title = renameDraft.trim();
    if (!activeThreadId || !title || title === activeThread?.title) {
      setRenaming(false);
      setRenameDraft(activeThread?.title ?? "");
      return;
    }
    try {
      const updated = await api<ChatThread>(`/api/chat/threads/${activeThreadId}`, {
        method: "PATCH",
        ...jsonBody({ title })
      });
      setThreads((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      setRenaming(false);
      setError("");
    } catch (err) {
      setError(errorMessage(err, "重命名失败"));
    }
  }

  function chooseImage(file?: File) {
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setError("截图只支持 PNG、JPEG 或 WebP。")
      return;
    }
    if (file.size > 4 * 1024 * 1024) {
      setError("截图不能超过 4 MB。")
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setImageDataUrl(typeof reader.result === "string" ? reader.result : "");
      setImageName(file.name);
      setError("");
    };
    reader.onerror = () => setError("截图读取失败，请重试。");
    reader.readAsDataURL(file);
  }

  function pasteImage(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageItem = Array.from(event.clipboardData.items).find(
      (item) => item.kind === "file" && item.type.startsWith("image/")
    );
    const file = imageItem?.getAsFile();
    if (!file) return;
    const namedFile = file.name
      ? file
      : new File([file], `剪贴板截图-${new Date().toISOString().replace(/:/g, "-")}.${file.type.split("/")[1] || "png"}`, { type: file.type });
    chooseImage(namedFile);
    if (!event.clipboardData.getData("text/plain")) event.preventDefault();
  }

  return (
    <section className="chat-shell" aria-label="决策聊天">
      <aside className="chat-sidebar">
        <div className="chat-sidebar-head">
          <div><strong>对话</strong><small>刷新页面后仍会保留</small></div>
          <button className="small-action" type="button" onClick={() => createThread("general")} disabled={sending}><Plus size={15} /> 新对话</button>
        </div>
        <div className="chat-job-create">
          <select value={jobId} onChange={(event) => setJobId(event.target.value)} aria-label="选择岗位创建聊天">
            <option value="">选择岗位专属聊天…</option>
            {jobs.map((job) => <option key={job.id} value={job.id}>{job.company_name} · {job.title}</option>)}
          </select>
          <button className="small-action" type="button" onClick={() => createThread("job")} disabled={!jobId || sending}>创建 / 打开</button>
        </div>
        <div className="chat-thread-list">
          {threads.map((thread) => (
            <button type="button" key={thread.id} className={thread.id === activeThreadId ? "chat-thread active" : "chat-thread"} onClick={() => setActiveThreadId(thread.id)} disabled={sending}>
              <span>{thread.kind === "job" ? "岗位" : thread.kind === "ingest" ? "入库" : "通用"}</span>
              <strong>{thread.title}</strong>
              <small>{thread.last_message || "还没有消息"}</small>
            </button>
          ))}
          {!loading && !threads.length && <p className="muted chat-empty-copy">先创建一个通用聊天，或者给某个岗位开专属聊天。</p>}
        </div>
      </aside>

      <div className="chat-main">
        {activeThread ? (
          <>
            <header className="chat-head">
              <div>
                <div className="chat-title-line">
                  <span className="chat-kind">{activeThread.kind === "job" ? "岗位聊天" : activeThread.kind === "ingest" ? "入库候选" : "通用聊天"}</span>
                  {renaming ? (
                    <form className="chat-rename" onSubmit={renameThread}>
                      <input autoFocus maxLength={120} value={renameDraft} onChange={(event) => setRenameDraft(event.target.value)} aria-label="聊天名称" />
                      <button className="small-action" type="submit" disabled={!renameDraft.trim()}>保存</button>
                      <button className="icon-button compact" type="button" title="取消重命名" onClick={() => { setRenaming(false); setRenameDraft(activeThread.title); }}><X size={14} /></button>
                    </form>
                  ) : (
                    <>
                      <h2>{activeThread.title}</h2>
                      <button className="icon-button compact chat-rename-trigger" type="button" title="重命名聊天" aria-label="重命名聊天" onClick={() => setRenaming(true)} disabled={sending}><Pencil size={14} /></button>
                    </>
                  )}
                </div>
                <p>规则优先，AI 可选；内容留在本机，启用 AI 时才发送本次材料与所需上下文。</p>
              </div>
              {activeJob && <button className="small-action" type="button" onClick={() => onOpenJob(activeJob)}>查看岗位</button>}
            </header>

            <div className="chat-messages" aria-live="polite">
              {!messages.length && !loading && (
                <div className="chat-welcome">
                  <MessageSquareText size={28} />
                  <h3>把你拿不准的事情直接丢进来</h3>
                  <p>可以粘贴 JD、招聘方回复、网页链接旁的正文，或描述你现在的约束。信息不足时，助手会明确告诉你还缺什么。</p>
                  <div className="chat-prompts">
                    {["这个岗位值不值得聊？我最应该先确认什么？", "招聘方这样回复，我现在怎么回？", "这件事有价值到需要沉淀吗？"].map((prompt) => (
                      <button type="button" className="small-action" key={prompt} onClick={() => setDraft(prompt)}>{prompt}</button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((message) => {
                const analysis = message.role === "assistant" ? message.metadata_json?.analysis : undefined;
                const candidates = message.role === "assistant" ? message.metadata_json?.candidates : undefined;
                return (
                  <article key={message.id} className={`chat-message ${message.role}`}>
                    <div className="chat-message-label">{message.role === "user" ? "你" : "助手"}</div>
                    <div className="chat-bubble">
                      <p>{analysis?.summary || message.content}</p>
                      {message.metadata_json?.attachment?.kind === "image" && (
                        <img className="chat-attachment" src={apiUrl(`/api/chat/attachments/${message.metadata_json.attachment.id}`)} alt={message.metadata_json.attachment.name || "聊天截图"} />
                      )}
                      {analysis && <DecisionAnalysisCard analysis={analysis} runStatus={message.metadata_json?.run_status ?? (message.metadata_json?.ai_used ? "completed" : "rules_only")} />}
                      {!!candidates?.length && (
                        <CandidateListCard
                          threadId={activeThreadId!}
                          messageId={message.id}
                          candidates={candidates}
                          onUpdated={(updated) => {
                            setMessages((items) => items.map((m) => (m.id === updated.id ? updated : m)));
                            void loadThreads(activeThreadId);
                          }}
                          onError={(msg) => setError(msg)}
                        />
                      )}
                    </div>
                  </article>
                );
              })}
              {pendingMessage && (
                <article className="chat-message user pending" aria-label="消息已发送，等待分析">
                  <div className="chat-message-label">你</div>
                  <div className="chat-bubble">
                    <p>{pendingMessage.content}</p>
                    {pendingMessage.imageDataUrl && <img className="chat-attachment" src={pendingMessage.imageDataUrl} alt={pendingMessage.imageName || "待分析截图"} />}
                    <small className="chat-pending-label"><CheckCircle2 size={13} />已发送</small>
                  </div>
                </article>
              )}
              {sending && (
                <article className="chat-message assistant">
                  <div className="chat-message-label">助手</div>
                  <div className="chat-bubble chat-thinking">
                    <ChatProgress stage={stage} aiAvailable={aiAvailable} />
                  </div>
                </article>
              )}
              <div ref={messageEndRef} />
            </div>

            {preview && (
              <div className="chat-preview" aria-label="发送给 AI 的内容预览">
                <div className="chat-preview-head">
                  <div>
                    <strong>{preview.ai_enabled ? "启用 AI 时会发送以下内容" : "当前未启用 AI，不会发送任何内容"}</strong>
                    <small>{preview.ai_enabled ? `模型 ${preview.model} · 固定上下文约 ${preview.context_chars_total} 字 · 最近对话 ${preview.conversation_count} 条` : "配置并测试连接后，这里会显示将要发送的上下文。"}</small>
                  </div>
                  <button type="button" className="icon-button compact" title="关闭预览" onClick={() => setPreview(null)}><X size={14} /></button>
                </div>
                {preview.ai_enabled && (
                  <>
                    <div className="chat-preview-sections">
                      {preview.sections.length ? preview.sections.map((section) => (
                        <details key={section.key} className="chat-preview-section">
                          <summary>{PREVIEW_SECTION_LABELS[section.key] ?? section.key}<span>{section.chars} 字</span></summary>
                          <pre>{section.content}</pre>
                        </details>
                      )) : <p className="muted">未配置外部上下文仓库，仅发送本地规则结果与对话。</p>}
                      {!!Object.keys(preview.job_context).length && (
                        <details className="chat-preview-section">
                          <summary>岗位事实<span>{Object.keys(preview.job_context).length} 项</span></summary>
                          <pre>{JSON.stringify(preview.job_context, null, 2)}</pre>
                        </details>
                      )}
                    </div>
                    <p className="chat-preview-note"><Info size={13} />{preview.note}</p>
                  </>
                )}
              </div>
            )}

            <form className="chat-composer" onSubmit={sendMessage}>
              {error && <div className="chat-error"><AlertCircle size={15} />{error}</div>}
              {imageDataUrl && (
                <div className="chat-attachment-preview">
                  <img src={imageDataUrl} alt={imageName || "待发送截图"} />
                  <span>{imageName}</span>
                  <button type="button" className="icon-button" title="移除截图" onClick={() => { setImageDataUrl(""); setImageName(""); }}><X size={15} /></button>
                </div>
              )}
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onPaste={pasteImage}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                rows={2}
                maxLength={12000}
                placeholder="粘贴材料或描述问题。Enter 发送，Shift + Enter 换行。"
                disabled={sending}
              />
              <div className="chat-composer-foot">
                <div className="chat-attachment-control">
                  <input ref={imageInputRef} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => { chooseImage(event.target.files?.[0]); event.target.value = ""; }} />
                  <button className="small-action" type="button" onClick={() => imageInputRef.current?.click()} disabled={sending}><ImagePlus size={15} />截图</button>
                  <button className="small-action" type="button" onClick={togglePreview} disabled={sending || previewLoading} title="查看启用 AI 时这一线程会发送给模型的固定上下文">
                    {previewLoading ? <Loader2 className="spin" size={15} /> : <Info size={15} />}{preview ? "收起预览" : "预览发送内容"}
                  </button>
                  <small>也可按 Ctrl + V 直接粘贴截图</small>
                </div>
                <button className="primary-action" disabled={(!draft.trim() && !imageDataUrl) || sending}>
                  {sending ? <Loader2 className="spin" size={17} /> : <Send size={17} />}{sending ? "分析中" : "发送"}
                </button>
              </div>
            </form>
          </>
        ) : (
          <div className="chat-welcome standalone">
            <MessageSquareText size={30} />
            <h3>先创建一条对话</h3>
            <p>通用聊天适合随手判断；岗位聊天会自动带上岗位库里的事实。</p>
            <button className="primary-action" type="button" onClick={() => createThread("general")}><Plus size={17} />新建通用聊天</button>
            {error && <div className="chat-error"><AlertCircle size={15} />{error}</div>}
          </div>
        )}
      </div>
    </section>
  );
}

// 阶段进度：结构化决策卡无法逐字流式，改为把等待拆成可见的三步。
// 规则检查始终第一步；询问模型仅在启用 AI 时出现；整理结果收尾。stage 由发送时的定时器推进。
function ChatProgress({ stage, aiAvailable }: { stage: number; aiAvailable: boolean }) {
  const steps = aiAvailable
    ? ["检查规则", "询问模型", "整理建议"]
    : ["检查规则", "整理建议"];
  // 无 AI 时只有两步：stage 0=检查规则，stage 2=整理建议，映射到 steps 下标 0/1。
  const activeIndex = aiAvailable ? stage : stage === 0 ? 0 : 1;
  return (
    <div className="chat-progress" aria-live="polite">
      {steps.map((label, index) => {
        const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "wait";
        return (
          <span key={label} className={`chat-progress-step ${state}`}>
            {state === "done" ? <CheckCircle2 size={14} /> : state === "active" ? <Loader2 className="spin" size={14} /> : <span className="chat-progress-dot" />}
            {label}
          </span>
        );
      })}
    </div>
  );
}

const PREVIEW_SECTION_LABELS: Record<string, string> = {
  decision_rules: "决策规则",
  profile: "个人画像",
  board: "求职看板",
};

type RunStatus = "completed" | "fallback" | "rules_only";

// 三态来源标记：AI 成功融合、AI 调用失败已回退、未启用 AI。
// 关键点是把「启用了但调用失败」和「从没配 AI」区分开，避免坏 key 时静默显示「仅规则」。
const RUN_STATUS_BADGE: Record<RunStatus, { label: string; tone: string; title: string }> = {
  completed: { label: "规则 + AI", tone: "ok", title: "规则先行，AI 已结合上下文补充。" },
  fallback: { label: "规则(AI 调用失败)", tone: "warn", title: "AI 已启用但本次调用失败，已回退到规则结果。可在侧栏「测试连接」查看原因。" },
  rules_only: { label: "仅规则", tone: "muted", title: "未启用 AI，仅使用本地规则。" },
};

function CandidateListCard({
  threadId,
  messageId,
  candidates,
  onUpdated,
  onError,
}: {
  threadId: number;
  messageId: number;
  candidates: IngestCandidate[];
  onUpdated: (message: ChatMessage) => void;
  onError: (message: string) => void;
}) {
  const pendingIndexes = candidates.map((c, i) => (c.status === "pending" || !c.status ? i : -1)).filter((i) => i >= 0);
  const [selected, setSelected] = useState<number[]>(pendingIndexes);
  const [busy, setBusy] = useState(false);

  function toggle(index: number) {
    if (candidates[index]?.status === "committed" || candidates[index]?.status === "skipped") return;
    setSelected((prev) => (prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index]));
  }

  async function commit(indexes: number[]) {
    setBusy(true);
    try {
      const reply = await api<{ assistant_message: ChatMessage }>(`/api/chat/threads/${threadId}/candidates/commit`, {
        method: "POST",
        ...jsonBody({ message_id: messageId, indexes }),
      });
      onUpdated(reply.assistant_message);
      const next = reply.assistant_message.metadata_json?.candidates ?? [];
      setSelected(next.map((c, i) => (c.status === "pending" || !c.status ? i : -1)).filter((i) => i >= 0));
    } catch (err) {
      onError(errorMessage(err, "入库失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="candidate-card" aria-label="入库候选">
      <div className="candidate-head">
        <strong>候选岗位</strong>
        <small>默认不入库；勾选后点「入库选中」</small>
      </div>
      <ul className="candidate-list">
        {candidates.map((item, index) => {
          const status = item.status || "pending";
          const disabled = status !== "pending";
          return (
            <li key={`${item.title}-${index}`} className={`candidate-item status-${status}`}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(index)}
                  disabled={disabled || busy}
                  onChange={() => toggle(index)}
                />
                <span className="candidate-body">
                  <strong>{item.title || "未命名岗位"}</strong>
                  <small>
                    {[item.company_name, item.salary_text, [item.city, item.area].filter(Boolean).join(" · "), item.source]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  {status === "committed" && item.job_id != null && <em>已入库 · #{item.job_id}</em>}
                  {status === "skipped" && <em>已跳过</em>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      {pendingIndexes.length > 0 && (
        <div className="candidate-actions">
          <button className="primary-action" type="button" disabled={busy || !selected.length} onClick={() => void commit(selected)}>
            {busy ? "处理中…" : `入库选中（${selected.length}）`}
          </button>
          <button className="small-action" type="button" disabled={busy} onClick={() => void commit([])}>
            全部跳过
          </button>
        </div>
      )}
    </div>
  );
}

function DecisionAnalysisCard({ analysis, runStatus }: { analysis: DecisionAnalysis; runStatus: RunStatus }) {
  const checks = analysis.rule_checks ?? [];
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const badge = RUN_STATUS_BADGE[runStatus] ?? RUN_STATUS_BADGE.rules_only;

  async function copyReplyDraft() {
    const ok = await copyToClipboard(analysis.reply_draft);
    setCopyState(ok ? "copied" : "error");
    window.setTimeout(() => setCopyState("idle"), 1600);
  }

  return (
    <div className="decision-card">
      <div className="decision-head">
        <span className={`priority priority-${analysis.priority === "待确认" ? "unknown" : analysis.priority.toLowerCase()}`}>{analysis.priority}</span>
        <strong>{analysis.direction}</strong>
        <small className={`run-badge run-badge-${badge.tone}`} title={badge.title}>{badge.label}</small>
      </div>
      <div className="decision-next"><span>唯一下一步 · {analysis.next_action}</span><strong>{analysis.action_text}</strong></div>
      {!!checks.length && (
        <div className="decision-checks">
          {checks.map((check) => (
            <div className={`decision-check ${check.status}`} key={check.code} title={check.detail}>
              <span>{check.status === "pass" ? "✓" : check.status === "fail" ? "×" : "?"}</span>
              <div><strong>{check.label}</strong><small>{check.detail}</small></div>
            </div>
          ))}
        </div>
      )}
      {(analysis.reasons?.length > 0 || analysis.risks?.length > 0 || analysis.uncertainties?.length > 0) && (
        <div className="decision-columns">
          <div><span>判断依据</span><ul>{analysis.reasons?.map((item) => <li key={item}>{item}</li>)}</ul></div>
          <div><span>风险 / 待确认</span><ul>{[...(analysis.risks ?? []), ...(analysis.uncertainties ?? [])].map((item) => <li key={item}>{item}</li>)}</ul></div>
        </div>
      )}
      {analysis.reply_draft && (
        <div className="decision-draft">
          <span>可发送草稿</span><p>{analysis.reply_draft}</p>
          <button className={`small-action copy-feedback ${copyState}`} type="button" onClick={copyReplyDraft} aria-live="polite">
            {copyState === "copied" ? <CheckCircle2 size={14} /> : <Copy size={14} />}
            {copyState === "copied" ? "已复制" : copyState === "error" ? "复制失败" : "复制"}
          </button>
        </div>
      )}
      <p className="decision-boundary">{analysis.pipeline_recommendation?.reason || "当前为只读建议，不会自动执行。"}</p>
    </div>
  );
}

function JobsView({
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
                <span className={scoreClass(job.latest_score?.total)}>{job.latest_score?.total ?? "-"}</span>
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

function CompaniesView({
  companies,
  jobs,
  onOpenJob
}: {
  companies: Company[];
  jobs: Job[];
  onOpenJob: (job: Job) => void;
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
              <div>
                <h3>{company.name}</h3>
                <p>{[company.industry, company.size, company.stage].filter(Boolean).join(" · ") || "公司画像待补充"}</p>
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

function PrepView({
  jobs,
  drafts,
  onOpen
}: {
  jobs: Job[];
  drafts: Draft[];
  onOpen: (job: Job) => void;
}) {
  const preppedJobIds = new Set(drafts.map((draft) => draft.job_id).filter(Boolean));
  return (
    <section className="split-view">
      <div className="content-panel queue-panel">
        <div className="section-title">
          <FileQuestion size={18} />
          <h2>准备队列</h2>
        </div>
        <div className="rank-list scroll-list">
          {jobs
            .filter((job) => job.status !== "rejected")
            .map((job) => (
              <button key={job.id} className="rank-row" onClick={() => onOpen(job)}>
                <span className={preppedJobIds.has(job.id) ? "pill good" : "pill"}>{preppedJobIds.has(job.id) ? "已生成" : "待生成"}</span>
                <strong>{job.title}</strong>
                <small>{job.company_name}</small>
              </button>
            ))}
        </div>
      </div>
      <div className="content-panel queue-panel">
        <div className="section-title">
          <MessageSquareText size={18} />
          <h2>准备素材</h2>
        </div>
        <div className="draft-list scroll-list">
          {drafts.map((draft) => (
            <article key={draft.id} className="draft-item">
              <span>{draftKindLabel(draft.kind)}</span>
              <p>{draft.content}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function TasksView({
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

function InterviewsView({
  interviews,
  jobs,
  busy,
  onOpenJob,
  onDelete,
  onCopyMarkdown
}: {
  interviews: InterviewLog[];
  jobs: Job[];
  busy: BusyState;
  onOpenJob: (job: Job) => void;
  onDelete: (log: InterviewLog) => Promise<void>;
  onCopyMarkdown: (log: InterviewLog) => Promise<void>;
}) {
  return (
    <section className="content-panel interviews-panel">
      <div className="list-summary">
        <strong>面试复盘</strong>
      </div>
      {interviews.length ? (
        <div className="interview-list">
          {interviews.map((log) => {
            const job = jobs.find((item) => item.id === log.job_id);
            return (
              <div key={log.id} className="interview-list-item">
                <InterviewCard log={log} job={job} busy={busy} onDelete={onDelete} onCopyMarkdown={onCopyMarkdown} showJob />
                {job && (
                  <button className="small-action" onClick={() => onOpenJob(job)}>
                    打开岗位
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="muted">还没有面试复盘。面试结束后，在岗位抽屉的「面试复盘」里记录一次，这里会按时间线跨岗位汇总，方便追溯与迭代。</p>
      )}
    </section>
  );
}

function InterviewCard({
  log,
  job,
  busy,
  onDelete,
  onCopyMarkdown,
  showJob = false
}: {
  log: InterviewLog;
  job?: Job | null;
  busy: BusyState;
  onDelete: (log: InterviewLog) => Promise<void>;
  onCopyMarkdown: (log: InterviewLog) => Promise<void>;
  showJob?: boolean;
}) {
  const hasScores = Object.keys(log.score_details ?? {}).length > 0;
  return (
    <article className="interview-card">
      <div className="interview-card-head">
        <div className="interview-card-title">
          <strong>{log.round}</strong>
          {showJob && job && <span className="muted">{job.company_name} · {job.title}</span>}
          {log.interview_date && <span className="muted">{log.interview_date}</span>}
          {log.interviewer && <span className="muted">{log.interviewer}</span>}
        </div>
        <div className="interview-card-meta">
          {log.opportunity_score != null && <span className={scoreClass(log.opportunity_score)}>{log.opportunity_score}</span>}
          {log.conclusion && <span className="status">{log.conclusion}</span>}
          <button className="icon-button compact" title="复制复盘 Markdown" onClick={() => onCopyMarkdown(log)}>
            <Copy size={14} />
          </button>
          <button className="icon-button compact" title="删除复盘" onClick={() => onDelete(log)} disabled={hasBusy(busy, `interview-${log.id}`)}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      {hasScores && (
        <p className="interview-dims">
          {OPPORTUNITY_DIMENSIONS.map((dim) => `${dim.key} ${log.score_details[dim.key] ?? 0}/${dim.weight}`).join(" · ")}
        </p>
      )}
      {log.real_picture && <TextBlock title="岗位真实画像" text={log.real_picture} />}
      {log.qa_review && <TextBlock title="问题复盘" text={log.qa_review} />}
      {log.weaknesses && <TextBlock title="暴露短板" text={log.weaknesses} />}
      {log.next_actions && <TextBlock title="下一步动作" text={log.next_actions} />}
      {log.follow_up && <TextBlock title="跟进话术" text={log.follow_up} />}
    </article>
  );
}

const EMPTY_INTERVIEW_FORM = {
  round: "一面",
  interview_date: "",
  interviewer: "",
  real_picture: "",
  score_details: {} as Record<string, number>,
  qa_review: "",
  weaknesses: "",
  next_actions: "",
  follow_up: ""
};

function InterviewForm({ busy, onAdd }: { busy: BusyState; onAdd: (payload: Partial<InterviewLog>) => Promise<void> }) {
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

function asConfigMap(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stringValue(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback = 0) {
  return typeof value === "number" ? value : fallback;
}

function booleanValue(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

function linesValue(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "";
}

function splitLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function setConfigValue(config: Record<string, unknown>, path: string[], value: unknown) {
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

const scoringFields = [
  ["role_match", "岗位匹配"],
  ["salary_city", "薪资城市"],
  ["growth", "成长空间"],
  ["stability", "稳定性"],
  ["reputation", "口碑"],
  ["commute_rest", "通勤休息"],
  ["interview_roi", "面试收益"]
] as const;

const configSections = [
  ["status", "运行状态"],
  ["ai", "AI"],
  ["sources", "采集来源"],
  ["profile", "个人画像"],
  ["scoring", "评分权重"],
  ["advanced", "高级"]
] as const;

type ConfigSection = (typeof configSections)[number][0];

function ConfigView({
  sources,
  runs,
  busy,
  profile,
  onNotify,
  onAiStatus,
  onCollectSource,
  onUpdateProfile
}: {
  sources: JobSourceStatus[];
  runs: SourceRun[];
  busy: BusyState;
  profile: UserProfile | null;
  onNotify: (kind: NoticeKind, message: string, details?: string[]) => void;
  onAiStatus: (status: AiStatus) => void;
  onCollectSource: (sourceKey: string, label: string, zeroFallback: string) => Promise<void>;
  onUpdateProfile: (event: FormEvent<HTMLFormElement>) => Promise<void>;
}) {
  const [payload, setPayload] = useState<AppConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [activeSection, setActiveSection] = useState<ConfigSection>("status");
  const [envExampleOpen, setEnvExampleOpen] = useState(false);
  useEscapeClose(envExampleOpen, () => setEnvExampleOpen(false));

  useEffect(() => {
    let active = true;
    api<AppConfig>("/api/config")
      .then((data) => {
        if (active) setPayload(data);
      })
      .catch((err) => {
        if (active) onNotify("error", errorMessage(err, "配置加载失败"));
      });
    return () => {
      active = false;
    };
  }, []);

  function updateConfig(path: string[], value: unknown) {
    setPayload((current) => (current ? { ...current, config: setConfigValue(current.config, path, value) } : current));
  }

  async function saveConfig(event: FormEvent) {
    event.preventDefault();
    if (!payload) return;
    if (scoringWeightInvalid) {
      setActiveSection("scoring");
      onNotify("error", `评分权重合计不能超过 100，当前为 ${scoringWeightTotalText}/100。`);
      return;
    }
    setSaving(true);
    try {
      const saved = await api<AppConfig>("/api/config", { method: "PUT", ...jsonBody({ config: payload.config }) });
      setPayload(saved);
      onNotify("success", "系统配置已保存。", saved.restart_recommended_after_save);
      const latestAi = await api<AiStatus>("/api/ai/status");
      onAiStatus(latestAi);
    } catch (err) {
      onNotify("error", errorMessage(err, "配置保存失败"));
    } finally {
      setSaving(false);
    }
  }

  if (!payload) {
    return (
      <section className="content-panel config-panel">
        <p className="muted">正在加载配置...</p>
      </section>
    );
  }

  const config = payload.config;
  const opencli = asConfigMap(config.opencli);
  const jobSources = asConfigMap(config.job_sources);
  const zhilianSource = asConfigMap(jobSources.zhilian);
  const bebee = asConfigMap(config.bebee);
  const wechat = asConfigMap(config.wechat);
  const wechatFetch = asConfigMap(wechat.fetch);
  const yuanbao = asConfigMap(wechat.yuanbao_automation);
  const ai = asConfigMap(config.ai);
  const scoring = asConfigMap(config.scoring);
  const weights = asConfigMap(scoring.weights);
  const scoringWeightTotal = scoringFields.reduce((sum, [key]) => sum + numberValue(weights[key], 0), 0);
  const scoringWeightTotalText = Number.isInteger(scoringWeightTotal) ? String(scoringWeightTotal) : scoringWeightTotal.toFixed(1);
  const scoringWeightInvalid = scoringWeightTotal > 100;
  const general = asConfigMap(config.general);
  const sourceByKey = (key: string) => sources.find((source) => source.key === key);
  const bossSource = sourceByKey("boss");
  const zhilianStatus = sourceByKey("zhilian");
  const bebeeStatus = sourceByKey("bebee");
  const wechatLabel = stringValue(wechat.source_label, "公众号");
  const latestWechatRun = runs.find((run) => run.source === wechatLabel);

  function sourceStatus(source?: JobSourceStatus) {
    if (!source) return <span className="status not_configured">未配置</span>;
    return <span className={`status ${source.status}`}>{source.enabled ? source.status : "disabled"}</span>;
  }

  function latestRunText(run?: SourceRun | null) {
    if (!run) return "未运行";
    return `${run.status} · ${run.fetched_count} 抓取 / ${run.created_count} 新增 / ${run.updated_count} 更新`;
  }

  function sourceRunButton(source?: JobSourceStatus) {
    if (!source) return null;
    const canRun = source.enabled && (source.configured || source.status === "host_import_required");
    return (
      <button
        type="button"
        className="small-action"
        disabled={hasAnyBusy(busy, [...GLOBAL_BUSY_KEYS]) || !canRun}
        onClick={() => onCollectSource(source.key, `${source.label}采集`, "本次没有采集到可用岗位。请检查来源配置和最近运行详情。")}
      >
        {hasBusy(busy, `source-${source.key}`) ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
        运行
      </button>
    );
  }

  const tabs = (
    <div className="config-tabs" role="tablist" aria-label="系统配置分区">
      {configSections.map(([key, label]) => (
        <button
          key={key}
          type="button"
          className={activeSection === key ? "active" : ""}
          onClick={() => setActiveSection(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );

  if (activeSection === "profile") {
    return (
      <section className="content-panel config-panel">
        <form className="config-layout" onSubmit={onUpdateProfile}>
          <div className="config-head">
            <div>
              <h2>个人画像</h2>
              <p>目标、技能、薪资与排除项，直接影响匹配评分</p>
            </div>
            <button className="primary-action" disabled={!profile || hasBusy(busy, "profile")}>
              {hasBusy(busy, "profile") ? "保存中…" : "保存画像"}
            </button>
          </div>
          {tabs}
          <div className="config-scroll">
            {profile ? (
              <div className="config-grid single-column">
                <label>
                  目标岗位
                  <input name="target_titles" defaultValue={profile.target_titles} />
                </label>
                <label>
                  目标城市
                  <input name="target_cities" defaultValue={profile.target_cities} />
                </label>
                <div className="inline-fields">
                  <label>
                    最低 K
                    <input name="salary_min_k" type="number" defaultValue={profile.salary_min_k} />
                  </label>
                  <label>
                    最高 K
                    <input name="salary_max_k" type="number" defaultValue={profile.salary_max_k} />
                  </label>
                </div>
                <label>
                  技能
                  <textarea name="skills" defaultValue={profile.skills} />
                </label>
                <label>
                  优势
                  <textarea name="strengths" defaultValue={profile.strengths} />
                </label>
                <label>
                  实际工作经历 / 项目成果
                  <textarea name="work_experience" className="large-textarea" defaultValue={profile.work_experience} />
                </label>
                <label>
                  排除项
                  <input name="dealbreakers" defaultValue={profile.dealbreakers} />
                </label>
                <label>
                  通勤偏好
                  <input name="commute_preferences" defaultValue={profile.commute_preferences} />
                </label>
              </div>
            ) : (
              <p className="muted">正在加载画像…</p>
            )}
          </div>
        </form>
      </section>
    );
  }

  return (
    <section className="content-panel config-panel">
      <form className="config-layout" onSubmit={saveConfig}>
        <div className="config-head">
          <div>
            <h2>系统配置</h2>
            <p>{payload.path}</p>
          </div>
          <button className="primary-action" disabled={saving || scoringWeightInvalid}>
            {saving ? "保存中..." : "保存配置"}
          </button>
        </div>

        {tabs}

        <div className="config-scroll">
          {activeSection === "status" && (
            <div className="config-section-stack">
              {payload.config_error && (
                <div className="config-alert warning" role="alert">
                  <AlertTriangle size={16} />
                  <span>{payload.config_error}</span>
                </div>
              )}
              <div className="config-status-grid">
                <div>
                  <span>OpenAI Key</span>
                  <strong>{payload.env.openai_api_key_configured ? "已配置" : "未配置"}</strong>
                </div>
                <div>
                  <span>Base URL</span>
                  <strong>{payload.env.openai_base_url_configured ? "已配置" : "默认"}</strong>
                </div>
                <div>
                  <span>模型</span>
                  <strong>{payload.env.openai_model}</strong>
                </div>
                <div>
                  <span>数据目录</span>
                  <strong>{stringValue(general.data_dir, "./data/job_one_stop")}</strong>
                </div>
                <div>
                  <span>数据库环境变量</span>
                  <strong>{payload.env.database_url_configured ? "已配置" : "默认 SQLite"}</strong>
                </div>
                <div>
                  <span>服务端口</span>
                  <strong>{payload.env.port}</strong>
                </div>
                <div>
                  <span>上传上限</span>
                  <strong>{payload.env.max_upload_mb} MB</strong>
                </div>
              </div>

              <div className="source-grid">
                {sources.map((source) => (
                  <article className="source-card" key={source.key}>
                    <div className="source-card-head">
                      <div>
                        <strong>{source.label}</strong>
                        <span>{source.kind}</span>
                      </div>
                      {sourceStatus(source)}
                    </div>
                    <p>{source.message}</p>
                    {source.config.host_collection?.script && <small>{source.config.host_collection.script}</small>}
                    <small>最近：{latestRunText(source.latest_run)}</small>
                    {sourceRunButton(source)}
                  </article>
                ))}
                <article className="source-card">
                  <div className="source-card-head">
                    <div>
                      <strong>{wechatLabel}</strong>
                      <span>wechat_article</span>
                    </div>
                    <span className={booleanValue(wechatFetch.enabled, true) ? "status ok" : "status disabled"}>
                      {booleanValue(wechatFetch.enabled, true) ? "ok" : "disabled"}
                    </span>
                  </div>
                  <p>链接或正文从导入弹窗提交。</p>
                  <small>最近：{latestRunText(latestWechatRun)}</small>
                </article>
              </div>
            </div>
          )}

          {activeSection === "ai" && (
            <div className="config-grid single-column">
              <fieldset>
                <legend>AI</legend>
                <label className="switch-field">
                  <input
                    type="checkbox"
                    checked={booleanValue(ai.enabled)}
                    onChange={(event) => updateConfig(["ai", "enabled"], event.target.checked)}
                  />
                  <span>启用 AI 兜底</span>
                </label>
                <label>
                  Provider
                  <input value={stringValue(ai.provider, "openai_compatible")} onChange={(event) => updateConfig(["ai", "provider"], event.target.value)} />
                </label>
                <div className="config-status-grid compact">
                  <div>
                    <span>OPENAI_API_KEY</span>
                    <strong>{payload.env.openai_api_key_configured ? "已配置" : "未配置"}</strong>
                  </div>
                  <div>
                    <span>OPENAI_BASE_URL</span>
                    <strong>{payload.env.openai_base_url_configured ? "已配置" : "默认"}</strong>
                  </div>
                  <div>
                    <span>OPENAI_MODEL</span>
                    <strong>{payload.env.openai_model}</strong>
                  </div>
                </div>
                <button type="button" className="small-action config-example-button" onClick={() => setEnvExampleOpen(true)}>
                  <Info size={14} />
                  配置示例
                </button>
              </fieldset>
            </div>
          )}

          {activeSection === "sources" && (
            <div className="source-grid editable-source-grid">
              <article className="source-card">
                <div className="source-card-head">
                  <div>
                    <strong>{bossSource?.label ?? "BOSS直聘"}</strong>
                    <span>opencli_csv</span>
                  </div>
                  {sourceStatus(bossSource)}
                </div>
                <p>{bossSource?.message ?? "未读取到来源状态。"}</p>
                {bossSource?.config.host_collection?.script && <small>{bossSource.config.host_collection.script}</small>}
                <small>最近：{latestRunText(bossSource?.latest_run)}</small>
                <label>
                  命令模板
                  <textarea
                    className="config-textarea command-textarea"
                    value={linesValue(opencli.boss_cmd)}
                    onChange={(event) => updateConfig(["opencli", "boss_cmd"], splitLines(event.target.value))}
                  />
                </label>
                {sourceRunButton(bossSource)}
              </article>

              <article className="source-card">
                <div className="source-card-head">
                  <div>
                    <strong>{stringValue(zhilianSource.label, "智联招聘")}</strong>
                    <span>opencli_csv</span>
                  </div>
                  {sourceStatus(zhilianStatus)}
                </div>
                <label className="switch-field">
                  <input
                    type="checkbox"
                    checked={booleanValue(zhilianSource.enabled)}
                    onChange={(event) => updateConfig(["job_sources", "zhilian", "enabled"], event.target.checked)}
                  />
                  <span>启用智联招聘模板</span>
                </label>
                <label>
                  来源名
                  <input
                    value={stringValue(zhilianSource.label, "智联招聘")}
                    onChange={(event) => updateConfig(["job_sources", "zhilian", "label"], event.target.value)}
                  />
                </label>
                <p>{zhilianStatus?.message ?? "未读取到来源状态。"}</p>
                {zhilianStatus?.config.host_collection?.script && <small>{zhilianStatus.config.host_collection.script}</small>}
                <small>最近：{latestRunText(zhilianStatus?.latest_run)}</small>
                <label>
                  命令模板
                  <textarea
                    className="config-textarea command-textarea"
                    value={linesValue(zhilianSource.command)}
                    onChange={(event) => updateConfig(["job_sources", "zhilian", "command"], splitLines(event.target.value))}
                  />
                </label>
                {sourceRunButton(zhilianStatus)}
              </article>

              <article className="source-card">
                <div className="source-card-head">
                  <div>
                    <strong>{stringValue(bebee.source_label, "beBee")}</strong>
                    <span>structured_pages</span>
                  </div>
                  {sourceStatus(bebeeStatus)}
                </div>
                <label className="switch-field">
                  <input
                    type="checkbox"
                    checked={booleanValue(bebee.enabled, true)}
                    onChange={(event) => updateConfig(["bebee", "enabled"], event.target.checked)}
                  />
                  <span>启用采集</span>
                </label>
                <label>
                  来源名
                  <input value={stringValue(bebee.source_label, "beBee")} onChange={(event) => updateConfig(["bebee", "source_label"], event.target.value)} />
                </label>
                <p>{bebeeStatus?.message ?? "未读取到来源状态。"}</p>
                <small>最近：{latestRunText(bebeeStatus?.latest_run)}</small>
                <label>
                  角色页 URL
                  <textarea
                    className="config-textarea"
                    value={linesValue(bebee.role_urls)}
                    onChange={(event) => updateConfig(["bebee", "role_urls"], splitLines(event.target.value))}
                  />
                </label>
                {sourceRunButton(bebeeStatus)}
              </article>

              <article className="source-card">
                <div className="source-card-head">
                  <div>
                    <strong>{wechatLabel}</strong>
                    <span>wechat_article</span>
                  </div>
                  <span className={booleanValue(wechatFetch.enabled, true) ? "status ok" : "status disabled"}>
                    {booleanValue(wechatFetch.enabled, true) ? "ok" : "disabled"}
                  </span>
                </div>
                <label>
                  来源名
                  <input value={wechatLabel} onChange={(event) => updateConfig(["wechat", "source_label"], event.target.value)} />
                </label>
                <label className="switch-field">
                  <input
                    type="checkbox"
                    checked={booleanValue(wechatFetch.enabled, true)}
                    onChange={(event) => updateConfig(["wechat", "fetch", "enabled"], event.target.checked)}
                  />
                  <span>服务端抓取正文</span>
                </label>
                <small>最近：{latestRunText(latestWechatRun)}</small>
              </article>
            </div>
          )}

          {activeSection === "scoring" && (
            <div className="config-grid single-column">
              <fieldset>
                <legend>评分权重</legend>
                <div className="fieldset-title-row">
                  <span className={scoringWeightInvalid ? "weight-total invalid" : "weight-total"}>
                    当前合计 {scoringWeightTotalText}/100
                  </span>
                </div>
                <div className="weights-grid">
                  {scoringFields.map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <input
                        type="number"
                        min={0}
                        value={numberValue(weights[key], 0)}
                        onChange={(event) => updateConfig(["scoring", "weights", key], Number(event.target.value))}
                      />
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>
          )}

          {activeSection === "advanced" && (
            <div className="config-grid single-column">
              <details className="advanced-details">
                <summary>数据目录与采集参数</summary>
                <div className="advanced-body">
                  <label>
                    数据目录
                    <input value={stringValue(general.data_dir, "./data/job_one_stop")} onChange={(event) => updateConfig(["general", "data_dir"], event.target.value)} />
                  </label>
                  <div className="inline-fields">
                    <label>
                      beBee 间隔秒
                      <input
                        type="number"
                        min={0}
                        value={numberValue(bebee.rate_limit_seconds, 3)}
                        onChange={(event) => updateConfig(["bebee", "rate_limit_seconds"], Number(event.target.value))}
                      />
                    </label>
                    <label>
                      beBee 超时秒
                      <input
                        type="number"
                        min={1}
                        value={numberValue(bebee.timeout_seconds, 20)}
                        onChange={(event) => updateConfig(["bebee", "timeout_seconds"], Number(event.target.value))}
                      />
                    </label>
                  </div>
                  <label>
                    beBee User-Agent
                    <textarea
                      className="config-textarea compact"
                      value={stringValue(bebee.user_agent)}
                      onChange={(event) => updateConfig(["bebee", "user_agent"], event.target.value)}
                    />
                  </label>
                </div>
              </details>

              <details className="advanced-details">
                <summary>公众号抓取</summary>
                <div className="advanced-body">
                  <div className="inline-fields">
                    <label>
                      间隔秒
                      <input
                        type="number"
                        min={0}
                        value={numberValue(wechatFetch.rate_limit_seconds, 3)}
                        onChange={(event) => updateConfig(["wechat", "fetch", "rate_limit_seconds"], Number(event.target.value))}
                      />
                    </label>
                    <label>
                      兜底阈值
                      <input
                        type="number"
                        min={0}
                        value={numberValue(wechat.min_jobs_before_llm_fallback, 1)}
                        onChange={(event) => updateConfig(["wechat", "min_jobs_before_llm_fallback"], Number(event.target.value))}
                      />
                    </label>
                  </div>
                  <label>
                    User-Agent
                    <textarea
                      className="config-textarea compact"
                      value={stringValue(wechatFetch.user_agent)}
                      onChange={(event) => updateConfig(["wechat", "fetch", "user_agent"], event.target.value)}
                    />
                  </label>
                </div>
              </details>

              <details className="advanced-details">
                <summary>元宝自动化</summary>
                <div className="advanced-body">
                  <label className="switch-field">
                    <input
                      type="checkbox"
                      checked={booleanValue(yuanbao.enabled)}
                      onChange={(event) => updateConfig(["wechat", "yuanbao_automation", "enabled"], event.target.checked)}
                    />
                    <span>启用元宝自动化</span>
                  </label>
                  <label>
                    登录目录
                    <input
                      value={stringValue(yuanbao.user_data_dir, "./data/.yuanbao")}
                      onChange={(event) => updateConfig(["wechat", "yuanbao_automation", "user_data_dir"], event.target.value)}
                    />
                  </label>
                  <label>
                    元宝提示词
                    <textarea
                      className="config-textarea"
                      value={stringValue(yuanbao.prompt_template)}
                      onChange={(event) => updateConfig(["wechat", "yuanbao_automation", "prompt_template"], event.target.value)}
                    />
                  </label>
                </div>
              </details>
            </div>
          )}
        </div>
      </form>

      {envExampleOpen && (
        <div className="modal-backdrop">
          <div className="modal config-example-modal" role="dialog" aria-modal="true" aria-labelledby="ai-config-example-title">
            <div className="modal-head">
              <div>
                <h2 id="ai-config-example-title">AI 配置示例</h2>
                <p className="muted">密钥只通过 `.env` 或容器环境变量提供。</p>
              </div>
              <button type="button" className="icon-button" onClick={() => setEnvExampleOpen(false)} title="关闭">
                <X size={18} />
              </button>
            </div>
            <pre className="env-snippet">{`OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_MODEL=${payload.env.openai_model}`}</pre>
            <div className="modal-notes">
              <p>Docker Compose 会读取根目录 `.env` 中的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，并把它们注入 app 容器。</p>
              <p>`config.yaml` 只保存 `ai.enabled` 和 provider 等非密钥配置；本页不会显示、保存或写回 API Key。</p>
              <p>修改 `.env` 后需要重启容器；修改镜像、依赖或构建参数后需要重建容器。</p>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function JobEditModal({
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

function JobEventsSection({
  events,
  busy,
  onAddEvent,
  onDeleteEvent
}: {
  events: ApplicationEvent[];
  busy: BusyState;
  onAddEvent: (payload: { event_type: string; event_date: string; channel?: string; note?: string }) => Promise<void>;
  onDeleteEvent: (event: ApplicationEvent) => Promise<void>;
}) {
  const [form, setForm] = useState({
    event_type: "applied",
    event_date: new Date().toISOString().slice(0, 10),
    channel: "",
    note: ""
  });
  return (
    <>
      <div className="event-list">
        {events.map((item) => (
          <article key={item.id} className="event-item">
            <div className="event-meta">
              <div>
                <strong>{applicationEventLabels[item.event_type] ?? item.event_type}</strong>
                <span>
                  {item.event_date}
                  {item.channel ? ` · ${item.channel}` : ""}
                </span>
              </div>
              <button
                type="button"
                className="icon-button compact"
                title="删除事件"
                onClick={() => onDeleteEvent(item)}
                disabled={hasBusy(busy, `job-event-${item.id}`)}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {item.note && <p className="event-note">{item.note}</p>}
          </article>
        ))}
        {!events.length && <p className="muted">还没有投递事件。记录投递、回复、约面、拒绝和 Offer 后，漏斗分析会更可信。</p>}
      </div>
      <form
        className="research-form event-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!form.event_date) return;
          onAddEvent(form).then(() =>
            setForm((current) => ({
              ...current,
              note: "",
              channel: ""
            }))
          );
        }}
      >
        <div className="inline-fields">
          <label>
            事件
            <select value={form.event_type} onChange={(event) => setForm({ ...form, event_type: event.target.value })}>
              {Object.entries(applicationEventLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            日期
            <input type="date" value={form.event_date} onChange={(event) => setForm({ ...form, event_date: event.target.value })} required />
          </label>
        </div>
        <label>
          渠道
          <input value={form.channel} onChange={(event) => setForm({ ...form, channel: event.target.value })} placeholder="BOSS / 邮件 / 微信 / 内推" />
        </label>
        <label>
          备注
          <textarea value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} placeholder="例如：已附带作品集、对方说下周约面" />
        </label>
        <button className="primary-action" disabled={hasBusy(busy, "job-event")}>
          <Plus size={16} />
          记录事件
        </button>
      </form>
    </>
  );
}

function JobDrawer({
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
  position
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
            {latestScore.hard_blocked && <strong className="danger-text">硬性条件阻断</strong>}
            {latestScore.details?.hard_reasons?.map((reason) => <p key={reason}>{reason}</p>)}
            <div className="dimension-list">
              {Object.entries(latestScore.details?.dimensions ?? {}).map(([key, value]) => (
                <div key={key} className="dimension-row">
                  <span className="dimension-copy">
                    <span>{dimensionLabel(key)}</span>
                    {value.note && <small>{value.note}</small>}
                  </span>
                  <meter value={value.score} max={value.weight} />
                  <strong>
                    {value.score}/{value.weight}
                  </strong>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <p className="muted">尚未评分</p>
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

function TextBlock({ title, text }: { title: string; text: string }) {
  const [copied, setCopied] = useState(false);
  async function copyText() {
    if (await copyToClipboard(text)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } else {
      setCopied(false);
    }
  }
  return (
    <article className="text-block">
      <div className="text-block-head">
        <strong>{title}</strong>
        <button type="button" className="icon-button compact" title={`复制${title}`} onClick={copyText}>
          {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
        </button>
      </div>
      <p>{text}</p>
    </article>
  );
}

function scoreClass(score?: number | null) {
  if (score == null) return "score-pill";
  if (score >= 80) return "score-pill high";
  if (score >= 65) return "score-pill mid";
  return "score-pill low";
}

function dimensionLabel(key: string) {
  return (
    {
      role_match: "岗位匹配",
      salary_city: "薪资/城市",
      growth: "成长性",
      stability: "稳定性",
      reputation: "口碑",
      commute_rest: "通勤/作息",
      interview_roi: "面试投入产出"
    }[key] ?? key
  );
}

export default App;
