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
  Download,
  FileQuestion,
  Gauge,
  Globe,
  Inbox,
  Info,
  ImagePlus,
  ListChecks,
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
import Tour from "./Tour";
import { CandidateListCard } from "./components/CandidateListCard";
import { ChatProgress } from "./components/ChatProgress";
import { DecisionAnalysisCard } from "./components/DecisionAnalysisCard";
import { ExportCenterModal } from "./components/ExportCenterModal";
import { InterviewCard } from "./components/InterviewCard";
import { InterviewForm } from "./components/InterviewForm";
import { JobEditModal } from "./components/JobEditModal";
import { JobEventsSection } from "./components/JobEventsSection";
import { JobPickerCombobox } from "./components/JobPickerCombobox";
import { NoticeBanner } from "./components/NoticeBanner";
import { PaginationControls } from "./components/PaginationControls";
import { ScoreBreakdown } from "./components/ScoreBreakdown";
import { ScoreChip } from "./components/ScoreChip";
import { SprintBriefModal } from "./components/SprintBriefModal";
import { StatBar } from "./components/StatBar";
import { TextBlock } from "./components/TextBlock";
import { UsageGuideModal } from "./components/UsageGuideModal";
import { CompaniesView } from "./views/CompaniesView";
import {
  ACTIVE_CHAT_THREAD_KEY,
  applicationEventLabels,
  CHAT_USE_AI_KEY,
  DEFAULT_SCORING_WEIGHTS,
  GLOBAL_BUSY_KEYS,
  JOB_PAGE_SIZE,
  PAGE_SIZE,
  PREVIEW_SECTION_LABELS,
  statuses,
  TOUR_STEPS,
  USAGE_GUIDE_SEEN_KEY,
  YUANBAO_PROMPT
} from "./lib/constants";
import {
  aiStatusLabel,
  asConfigMap,
  booleanValue,
  draftKindLabel,
  interviewLogToMarkdown,
  jobEditPayload,
  jobSourceLabels,
  linesValue,
  numberValue,
  rankedJobs,
  runDetailLines,
  runHasNoJobs,
  scoreClass,
  setConfigValue,
  skippedItems,
  sortedTasks,
  splitLines,
  stringValue
} from "./lib/format";
import type {
  AiProbeResult,
  AiStatus,
  AppConfig,
  ApplicationEvent,
  ChatContextPreview,
  ChatMessage,
  ChatReply,
  ChatThread,
  ChatThreadBatchDeleteReply,
  ChatThreadDetail,
  Company,
  ContextRepoStatus,
  Draft,
  FitScore,
  FollowUpTask,
  FunnelAnalytics,
  InterviewLog,
  InterviewPrep,
  Job,
  JobBulkUpdateResult,
  JobEditForm,
  JobSourceStatus,
  ManualJob,
  Notice,
  NoticeKind,
  ResearchItem,
  SourceRun,
  SprintBrief,
  StaleJob,
  UserProfile
} from "./types";

const navItems = [
  { id: "chat", label: "聊天", icon: MessageSquareText },
  { id: "jobs", label: "岗位", icon: BriefcaseBusiness },
  { id: "companies", label: "公司", icon: Building2 },
  { id: "prep", label: "准备", icon: FileQuestion },
  { id: "interviews", label: "复盘", icon: NotebookPen },
  { id: "tasks", label: "待办", icon: CalendarCheck },
  { id: "config", label: "设置", icon: SlidersHorizontal }
] as const;

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

const recruitmentStatusLabels: Record<string, string> = {
  active: "在招",
  closed: "已关闭",
  unknown: "未知"
};

const taskStatusLabels: Record<string, string> = {
  todo: "待办",
  done: "完成"
};

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
  const [contextStatus, setContextStatus] = useState<ContextRepoStatus | null>(null);
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
      { key: "ai", label: "AI 状态", run: () => api<AiStatus>("/api/ai/status"), apply: (value) => setAiStatus(value as AiStatus) },
      { key: "contextStatus", label: "个人上下文仓库", run: () => api<ContextRepoStatus>("/api/context/status"), apply: (value) => setContextStatus(value as ContextRepoStatus) }
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

  // 表格评分芯片的「尚未评分」空态入口：复用同一个评分端点，但不依赖 selectedJob/scores（那两个
  // 是抽屉专属状态），评分完直接刷新 jobs 列表，popover 下次打开即读到最新 latest_score。
  async function scoreJobById(jobId: number) {
    await runBusy(`score-${jobId}`, async () => {
      notify("info", "正在计算匹配评分…");
      try {
        const score = await api<FitScore>(`/api/jobs/${jobId}/score`, { method: "POST" });
        await reload(["jobs", "funnel"]);
        notify("success", `匹配评分已更新：${score.total} 分。`);
      } catch (err) {
        notify("error", errorMessage(err, "评分失败"));
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

  // 评分权重实际读的是 UserProfile.weights（见 scoring.py score_job），不是 config.yaml 的
  // scoring.weights —— 后者只在首次创建画像时当一次性默认值种子，改了也不会影响之后的评分。
  // 权重编辑因此必须走 /api/profile，和 updateProfile 用同一个持久化目标（同一条 UserProfile 行）。
  async function updateScoringWeights(weights: Record<string, number>) {
    await runBusy("profile-weights", async () => {
      notify("info", "正在保存评分权重…");
      try {
        const updated = await api<UserProfile>("/api/profile", { method: "PUT", ...jsonBody({ weights }) });
        setProfile(updated);
        notify("success", "评分权重已保存。", [
          "新权重对之后触发的评分生效；已有评分不会自动重算，可在岗位池评分芯片或岗位详情页点「重新评分」。"
        ]);
      } catch (err) {
        notify("error", errorMessage(err, "评分权重保存失败"));
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
        if (task.duplicate) {
          notify("info", "已存在相同待办，未重复新增。");
        } else {
          setTasks((items) => [task, ...items]);
          notify("success", "待办已新增。");
        }
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

      <main className="workspace">
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
          {activeNav === "chat" && (
            <ChatView jobs={jobs} onOpenJob={openJob} aiAvailable={Boolean(aiStatus?.available)} boardWriteEnabled={!!contextStatus?.available} />
          )}
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
              onScoreJob={scoreJobById}
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
              onUpdateWeights={updateScoringWeights}
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

function ChatView({
  jobs,
  onOpenJob,
  aiAvailable,
  boardWriteEnabled,
}: {
  jobs: Job[];
  onOpenJob: (job: Job) => void | Promise<void>;
  aiAvailable: boolean;
  boardWriteEnabled: boolean;
}) {
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
  const [deletingThreadId, setDeletingThreadId] = useState<number | null>(null);
  const [manageMode, setManageMode] = useState(false);
  const [selectedThreadIds, setSelectedThreadIds] = useState<Set<number>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ChatContextPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // 「本条不用 AI」：默认开(=可用 AI 时用 AI)，状态存本机，跨会话记住上次选择。
  const [useAiForMessage, setUseAiForMessage] = useState<boolean>(() => {
    try {
      const stored = window.localStorage.getItem(CHAT_USE_AI_KEY);
      return stored === null ? true : stored === "true";
    } catch {
      return true;
    }
  });
  // 阶段进度：结构化决策卡无法逐字流式，改为显式展示「检查规则 → 询问模型 → 整理结果」，让等待可见。
  const [stage, setStage] = useState(0);
  const stageTimers = useRef<number[]>([]);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const selectAllThreadsRef = useRef<HTMLInputElement>(null);

  const activeThread = threads.find((thread) => thread.id === activeThreadId) ?? null;
  const activeJob = activeThread?.job_id ? jobs.find((job) => job.id === activeThread.job_id) ?? null : null;
  // 这条消息实际会不会用到 AI：全局可用 且 本条开关没关。
  const effectiveAiForMessage = aiAvailable && useAiForMessage;

  useEffect(() => {
    try {
      window.localStorage.setItem(CHAT_USE_AI_KEY, String(useAiForMessage));
    } catch {
      // localStorage 只是记住上次选择的锦上添花，写入失败不影响本次发送。
    }
  }, [useAiForMessage]);

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
    // 阶段推进：规则先跑（立即），实际会用 AI 时约 0.4s 进入「询问模型」，兜底再显示「整理结果」。
    // 只是等待时的可见进度，不改变后端流程；请求返回时立刻清掉。本条关了 AI 时按「仅规则」的
    // 两步走，不显示不会发生的「询问模型」。
    setStage(0);
    stageTimers.current.forEach((id) => window.clearTimeout(id));
    stageTimers.current = effectiveAiForMessage
      ? [window.setTimeout(() => setStage(1), 400), window.setTimeout(() => setStage(2), 3500)]
      : [window.setTimeout(() => setStage(2), 250)];
    try {
      const reply = await api<ChatReply>(`/api/chat/threads/${activeThreadId}/messages`, {
        method: "POST",
        ...jsonBody({
          content,
          image_data_url: sentImageDataUrl || null,
          image_name: sentImageName || null,
          use_ai: useAiForMessage
        })
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

  async function deleteThread(thread: ChatThread) {
    if (!window.confirm(`删除聊天「${thread.title}」？会同时删除消息与截图附件，不可恢复。`)) return;
    setDeletingThreadId(thread.id);
    setError("");
    try {
      await api<{ deleted: boolean; id: number }>(`/api/chat/threads/${thread.id}`, { method: "DELETE" });
      setThreads((items) => items.filter((item) => item.id !== thread.id));
      if (activeThreadId === thread.id) {
        setActiveThreadId(null);
        setMessages([]);
      }
    } catch (err) {
      setError(errorMessage(err, "删除聊天失败"));
    } finally {
      setDeletingThreadId(null);
    }
  }

  function toggleManageMode() {
    setManageMode((value) => !value);
    setSelectedThreadIds(new Set());
  }

  function toggleThreadSelected(threadId: number, checked: boolean) {
    setSelectedThreadIds((current) => {
      const next = new Set(current);
      if (checked) next.add(threadId);
      else next.delete(threadId);
      return next;
    });
  }

  // 全选控件的三态：全选 / 半选(indeterminate) / 未选，随 selectedThreadIds 与 threads 派生，
  // 和逐行 checkbox 天然双向同步——任何一边变化都会触发重渲染重新算出这三个值。
  const allThreadsSelected = threads.length > 0 && threads.every((thread) => selectedThreadIds.has(thread.id));
  const someThreadsSelected = selectedThreadIds.size > 0 && !allThreadsSelected;

  useEffect(() => {
    if (selectAllThreadsRef.current) selectAllThreadsRef.current.indeterminate = someThreadsSelected;
  }, [someThreadsSelected]);

  function toggleAllThreads(checked: boolean) {
    setSelectedThreadIds(checked ? new Set(threads.map((thread) => thread.id)) : new Set());
  }

  async function batchDeleteThreads() {
    const ids = Array.from(selectedThreadIds);
    if (!ids.length || batchDeleting) return;
    if (!window.confirm(`将永久删除 ${ids.length} 个对话及其消息、截图附件，不可恢复。`)) return;
    setBatchDeleting(true);
    setError("");
    try {
      await api<ChatThreadBatchDeleteReply>("/api/chat/threads/batch-delete", {
        method: "POST",
        ...jsonBody({ ids }),
      });
      const deletedIds = new Set(ids);
      setThreads((items) => items.filter((item) => !deletedIds.has(item.id)));
      if (activeThreadId != null && deletedIds.has(activeThreadId)) {
        setActiveThreadId(null);
        setMessages([]);
      }
      setManageMode(false);
      setSelectedThreadIds(new Set());
    } catch (err) {
      setError(errorMessage(err, "批量删除失败"));
    } finally {
      setBatchDeleting(false);
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
          <div className="row-actions">
            <button
              type="button"
              className={manageMode ? "icon-button compact marked" : "icon-button compact"}
              title={manageMode ? "退出管理" : "管理对话"}
              aria-label={manageMode ? "退出管理" : "管理对话"}
              onClick={toggleManageMode}
              disabled={sending}
            >
              <ListChecks size={15} />
            </button>
            <button className="small-action" type="button" onClick={() => createThread("general")} disabled={sending}><Plus size={15} /> 新对话</button>
          </div>
        </div>
        <div className="chat-job-create">
          <JobPickerCombobox jobs={jobs} value={jobId} onChange={setJobId} disabled={sending} />
          <button className="small-action" type="button" onClick={() => createThread("job")} disabled={!jobId || sending}>创建 / 打开</button>
        </div>
        <div className="chat-thread-list">
          {threads.map((thread) => (
            <div className="chat-thread-row" key={thread.id}>
              <button type="button" className={thread.id === activeThreadId ? "chat-thread active" : "chat-thread"} onClick={() => setActiveThreadId(thread.id)} disabled={sending}>
                <span>{thread.kind === "job" ? "岗位" : thread.kind === "ingest" ? "入库" : "通用"}</span>
                <strong>{thread.title}</strong>
                <small>{thread.last_message || "还没有消息"}</small>
              </button>
              {manageMode ? (
                <span className="chat-thread-select">
                  <input
                    type="checkbox"
                    checked={selectedThreadIds.has(thread.id)}
                    onChange={(event) => toggleThreadSelected(thread.id, event.target.checked)}
                    aria-label={`选择聊天 ${thread.title}`}
                  />
                </span>
              ) : (
                <button
                  type="button"
                  className="icon-button compact chat-thread-delete"
                  title="删除聊天"
                  aria-label={`删除聊天 ${thread.title}`}
                  onClick={() => deleteThread(thread)}
                  disabled={sending || deletingThreadId === thread.id}
                >
                  {deletingThreadId === thread.id ? <Loader2 className="spin" size={13} /> : <Trash2 size={13} />}
                </button>
              )}
            </div>
          ))}
          {!loading && !threads.length && <p className="muted chat-empty-copy">先创建一个通用聊天，或者给某个岗位开专属聊天。</p>}
        </div>
        {manageMode && (
          <div className="chat-thread-batch-bar">
            <div className="chat-thread-batch-top">
              <label className="chat-select-all">
                <input
                  ref={selectAllThreadsRef}
                  type="checkbox"
                  checked={allThreadsSelected}
                  onChange={(event) => toggleAllThreads(event.target.checked)}
                  disabled={!threads.length}
                  aria-label="全选对话"
                />
                全选
              </label>
              <span>已选 {selectedThreadIds.size} 个</span>
            </div>
            <button className="primary-action" type="button" disabled={!selectedThreadIds.size || batchDeleting} onClick={() => void batchDeleteThreads()}>
              {batchDeleting ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
              删除选中（{selectedThreadIds.size}）
            </button>
          </div>
        )}
      </aside>

      <div className="chat-main">
        {activeThread ? (
          <>
            <header className="chat-head">
              <div className="chat-head-info">
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
                          boardWriteEnabled={boardWriteEnabled}
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
                    <ChatProgress stage={stage} aiAvailable={effectiveAiForMessage} />
                  </div>
                </article>
              )}
              <div ref={messageEndRef} />
            </div>

            {preview && (
              <div className="chat-preview" aria-label="发送给 AI 的内容预览">
                <div className="chat-preview-head">
                  <div>
                    <strong>
                      {aiAvailable && !useAiForMessage
                        ? "本条不发送任何内容给 AI"
                        : preview.ai_enabled
                        ? "启用 AI 时会发送以下内容"
                        : "当前未启用 AI，不会发送任何内容"}
                    </strong>
                    <small>
                      {aiAvailable && !useAiForMessage
                        ? "已为这一条关闭「本条用 AI」，只走本地规则；重新勾选后即可恢复。"
                        : preview.ai_enabled
                        ? `模型 ${preview.model} · 固定上下文约 ${preview.context_chars_total} 字 · 最近对话 ${preview.conversation_count} 条`
                        : "配置并测试连接后，这里会显示将要发送的上下文。"}
                    </small>
                  </div>
                  <button type="button" className="icon-button compact" title="关闭预览" onClick={() => setPreview(null)}><X size={14} /></button>
                </div>
                {preview.ai_enabled && useAiForMessage && (
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
                  {aiAvailable && (
                    <label className="chat-ai-toggle" title="关闭后，这一条只走规则引擎，不会发送给 AI；下一条可以再打开">
                      <input
                        type="checkbox"
                        checked={useAiForMessage}
                        disabled={sending}
                        onChange={(event) => setUseAiForMessage(event.target.checked)}
                      />
                      本条用 AI
                    </label>
                  )}
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
  onUpdateProfile,
  onUpdateWeights
}: {
  sources: JobSourceStatus[];
  runs: SourceRun[];
  busy: BusyState;
  profile: UserProfile | null;
  onNotify: (kind: NoticeKind, message: string, details?: string[]) => void;
  onAiStatus: (status: AiStatus) => void;
  onCollectSource: (sourceKey: string, label: string, zeroFallback: string) => Promise<void>;
  onUpdateProfile: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  onUpdateWeights: (weights: Record<string, number>) => Promise<void>;
}) {
  const [payload, setPayload] = useState<AppConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [activeSection, setActiveSection] = useState<ConfigSection>("status");
  const [envExampleOpen, setEnvExampleOpen] = useState(false);
  useEscapeClose(envExampleOpen, () => setEnvExampleOpen(false));
  // 评分权重实际存在 UserProfile.weights（见 updateScoringWeights 的注释），是独立于
  // config.yaml 的一条草稿状态：从 profile 首次可用时播种一次，之后只由用户在这个 tab 里编辑，
  // 不随其它 tab 的 config 拉取/保存被打断。
  const [weightsDraft, setWeightsDraft] = useState<Record<string, number> | null>(null);
  useEffect(() => {
    if (profile && weightsDraft === null) {
      setWeightsDraft({ ...DEFAULT_SCORING_WEIGHTS, ...(profile.weights ?? {}) });
    }
  }, [profile, weightsDraft]);

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

  if (activeSection === "scoring") {
    // 评分权重存在 UserProfile.weights，和「个人画像」共享同一条数据库行，但用途和字段独立，
    // 所以照抄 profile 这一段的写法：单独的 <form>、单独的保存按钮，不挂在下面 config.yaml
    // 那个通用表单上——那个表单点保存不会碰权重，混在一起点了也没反应，容易误导。
    const weightsTotal = weightsDraft ? scoringFields.reduce((sum, [key]) => sum + numberValue(weightsDraft[key], 0), 0) : 0;
    const weightsTotalText = Number.isInteger(weightsTotal) ? String(weightsTotal) : weightsTotal.toFixed(1);
    const weightsTotalInvalid = weightsTotal > 100;

    async function submitWeights(event: FormEvent) {
      event.preventDefault();
      if (!weightsDraft) return;
      if (weightsTotalInvalid) {
        onNotify("error", `评分权重合计不能超过 100，当前为 ${weightsTotalText}/100。`);
        return;
      }
      await onUpdateWeights(weightsDraft);
    }

    return (
      <section className="content-panel config-panel">
        <form className="config-layout" onSubmit={submitWeights}>
          <div className="config-head">
            <div>
              <h2>评分权重</h2>
              <p>7 个维度的权重，决定匹配评分怎么算；评分只是排序辅助，不会自动过滤或决定去留</p>
            </div>
            <button className="primary-action" disabled={!weightsDraft || hasBusy(busy, "profile-weights") || weightsTotalInvalid}>
              {hasBusy(busy, "profile-weights") ? "保存中…" : "保存权重"}
            </button>
          </div>
          {tabs}
          <div className="config-scroll">
            <div className="config-grid single-column">
              <fieldset>
                <legend>评分权重</legend>
                <div className="fieldset-title-row">
                  <span className={weightsTotalInvalid ? "weight-total invalid" : "weight-total"}>
                    当前合计 {weightsTotalText}/100
                  </span>
                </div>
                {weightsDraft ? (
                  <div className="weights-grid">
                    {scoringFields.map(([key, label]) => (
                      <label key={key}>
                        {label}
                        <input
                          type="number"
                          min={0}
                          value={numberValue(weightsDraft[key], 0)}
                          onChange={(event) => {
                            const next = Number(event.target.value);
                            setWeightsDraft((current) => ({ ...(current ?? DEFAULT_SCORING_WEIGHTS), [key]: next }));
                          }}
                        />
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="muted">正在加载画像…</p>
                )}
                <p className="muted weight-rescore-hint">
                  保存后新权重只对之后触发的评分生效——已有评分不会自动重算；需要的话，去岗位池评分芯片
                  或岗位详情页评分区对单个岗位点「重新评分」。当前没有「按新权重批量重评全部」的入口，逐个触发即可。
                </p>
              </fieldset>
            </div>
          </div>
        </form>
      </section>
    );
  }

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
          <button className="primary-action" disabled={saving}>
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

export default App;
