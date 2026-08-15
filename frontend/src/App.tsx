import {
  AlertCircle,
  BriefcaseBusiness,
  Building2,
  CalendarCheck,
  CheckCircle2,
  ClipboardList,
  Download,
  FileQuestion,
  Globe,
  Info,
  Loader2,
  MessageSquareText,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RefreshCw,
  SlidersHorizontal,
  Upload,
  X,
  Trash2
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, copyToClipboard, downloadApiFile, errorMessage, jsonBody } from "./api";
import { hasAnyBusy, hasBusy, useBusyState } from "./hooks/useBusyState";
import { isTypingElement, useEscapeClose } from "./hooks/useEscapeClose";
import Tour from "./Tour";
import { ExportCenterModal } from "./components/ExportCenterModal";
import { JobEditModal } from "./components/JobEditModal";
import { NoticeBanner } from "./components/NoticeBanner";
import { SprintBriefModal } from "./components/SprintBriefModal";
import { StatBar } from "./components/StatBar";
import { UsageGuideModal } from "./components/UsageGuideModal";
import { ChatView } from "./views/ChatView";
import { CompaniesView } from "./views/CompaniesView";
import { ConfigView } from "./views/ConfigView";
import { InterviewsView } from "./views/InterviewsView";
import { JobDrawer } from "./views/JobDrawer";
import { JobsView } from "./views/JobsView";
import { PrepView } from "./views/PrepView";
import { TasksView } from "./views/TasksView";
import { TrashView } from "./views/TrashView";
import {
  applicationEventLabels,
  GLOBAL_BUSY_KEYS,
  SIDEBAR_COLLAPSED_KEY,
  TOUR_STEPS,
  USAGE_GUIDE_SEEN_KEY,
  YUANBAO_PROMPT
} from "./lib/constants";
import {
  aiStatusLabel,
  interviewLogToMarkdown,
  jobEditPayload,
  jobSourceLabels,
  rankedJobs,
  runCountsText,
  runDetailLines,
  runHasNoJobs,
  skippedItems
} from "./lib/format";
import type {
  AiProbeResult,
  AiStatus,
  ApplicationEvent,
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
  { id: "trash", label: "回收站", icon: Trash2 },
  { id: "config", label: "设置", icon: SlidersHorizontal }
] as const;

function App() {
  const [activeNav, setActiveNav] = useState<(typeof navItems)[number]["id"]>("chat");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
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
  const [trashedJobs, setTrashedJobs] = useState<Job[]>([]);
  const [trashedCompanies, setTrashedCompanies] = useState<Company[]>([]);
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
    const pending = run.raw_config?.pending;
    const details = runDetailLines(run);
    if (skipped) details.push(`另有 ${skipped} 个页面/链接被跳过，可查看最近采集或弹窗详情。`);
    notify(
      pending ? "info" : "success",
      pending
        ? `${label}完成：抓到 ${run.fetched_count} 条，${pending} 条待筛。去「聊天」里的采集线索勾选入库。`
        : `${label}完成：抓到 ${run.fetched_count} 条，没有新岗位待筛（已在池的 ${run.updated_count} 条已刷新）。`,
      details.length ? details : undefined
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
    // 核心数据每次都加载；非核心数据按需加载（传 keys 或视图激活时）。
    const CORE_KEYS = ["jobs", "funnel", "profile", "ai", "sources", "contextStatus"];
    const active = keys
      ? tasks.filter((task) => keys.includes(task.key))
      : tasks.filter((task) => CORE_KEYS.includes(task.key));
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

  async function reloadTrash() {
    try {
      const [jobs, companies] = await Promise.all([
        api<Job[]>("/api/trash/jobs").catch(() => []),
        api<Company[]>("/api/trash/companies").catch(() => []),
      ]);
      setTrashedJobs(jobs);
      setTrashedCompanies(companies);
    } catch {
      // ignore
    }
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
    // 等待后端健康检查通过后再加载数据（Tauri 启动时后端可能需要几秒）
    async function waitForBackend() {
      for (let i = 0; i < 20; i++) {
        try {
          await api("/api/health");
          break;
        } catch {
          await new Promise(resolve => setTimeout(resolve, 500));
        }
      }
      await loadAll().catch((err) => notify("error", errorMessage(err, "加载数据失败")));
    }
    waitForBackend();
  }, []);

  useEffect(() => {
    if (activeNav === "trash") {
      reloadTrash();
      return;
    }
    // 按视图按需加载非核心数据，避免首屏打 12 个请求
    const viewKeys: Record<string, string[]> = {
      companies: ["companies"],
      prep: ["drafts"],
      interviews: ["interviews"],
      tasks: ["tasks", "stale"],
    };
    const keys = viewKeys[activeNav];
    if (keys) reload(keys);
  }, [activeNav]);

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
      // 侧栏底部那行结果容易被折叠/截断看不到，这里再弹一条醒目通知，确保测试反馈一定可见。
      if (result.ok) {
        notify("success", `AI 连接成功 · ${result.model}`, result.latency_ms != null ? [`响应 ${result.latency_ms}ms`] : undefined);
      } else {
        notify("error", "AI 连接失败", [result.reason]);
      }
    } catch (err) {
      const message = errorMessage(err, "测试请求失败");
      setAiProbe({ ok: false, stage: "call", reason: message, model: aiStatus?.model ?? "" });
      notify("error", "AI 测试请求失败", [message]);
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

  async function bulkDeleteJobs(ids: number[]) {
    if (!ids.length) return;
    if (!window.confirm(`将选中的 ${ids.length} 个岗位移入回收站？`)) return;
    await runBusy("bulk", async () => {
      try {
        const result = await api<{ deleted: number }>("/api/jobs/bulk-delete", { method: "POST", ...jsonBody({ ids }) });
        setJobs((items) => items.filter((item) => !ids.includes(item.id)));
        notify("info", `已移入回收站：${result.deleted} 个岗位`, ["可在「回收站」恢复或永久删除。"]);
        await reloadTrash();
      } catch (err) {
        notify("error", errorMessage(err, "批量删除失败"));
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

  async function deleteJob(job: Job) {
    if (!window.confirm(`将「${job.company_name} · ${job.title}」移入回收站？可在回收站恢复或永久删除。`)) return;
    await runBusy(`delete-job-${job.id}`, async () => {
      try {
        await api<{ deleted: boolean; id: number }>(`/api/jobs/${job.id}`, { method: "DELETE" });
        closeJobDrawer();
        await loadAll();
        await reloadTrash();
        notify("info", `已移入回收站：${job.company_name} · ${job.title}`, ["可在「回收站」恢复或永久删除。"]);
      } catch (err) {
        notify("error", errorMessage(err, "删除失败"));
      }
    });
  }

  async function deleteCompany(company: Company) {
    if (!window.confirm(`将「${company.name}」移入回收站？岗位不会被删除，只是公司档案不再显示。`)) return;
    try {
      await api<{ deleted: boolean; id: number }>(`/api/companies/${company.id}`, { method: "DELETE" });
      await loadAll();
      await reloadTrash();
      notify("info", `已移入回收站：${company.name}`);
    } catch (err) {
      notify("error", errorMessage(err, "删除失败"));
    }
  }

  return (
    <div className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <aside className="sidebar" aria-label="主导航">
        <div className="brand">
          <div className="brand-mark" title="job-one-stop · 本地求职助手" aria-label="job-one-stop · 本地求职助手">J1</div>
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() =>
              setSidebarCollapsed((value) => {
                const next = !value;
                try {
                  window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
                } catch {
                  // localStorage 不可用时忽略，仅本次会话生效。
                }
                return next;
              })
            }
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
        <div className="run-strip">
          <span>{latestRun ? `最近采集 · ${latestRun.source}` : "最近采集"}</span>
          <strong>{latestRun?.status ?? "未运行"}</strong>
          <small>
            {latestRun
              ? `${runCountsText(latestRun)}${latestSkipped ? ` / ${latestSkipped} 跳过` : ""}`
              : "等待首次采集"}
          </small>
        </div>
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
        <header className="topbar">
          <div>
            <h1>{navItems.find((item) => item.id === activeNav)?.label}</h1>
            <p>
              {activeNav === "chat"
                ? "把拿不准的事丢进来,先按规则判断再继续管理岗位。"
                : "岗位发现、公司证据、匹配评分、准备材料都留在本机。"}
            </p>
          </div>
          {activeNav !== "chat" && (
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
          )}
        </header>

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
              onBulkDelete={bulkDeleteJobs}
              onScoreJob={scoreJobById}
              busy={busy}
              onExport={() => exportFile(`/api/exports/jobs?format=csv&status=${status === "all" ? "" : status}&source=${sourceFilter === "all" ? "" : sourceFilter}`, "jobs.csv", "岗位池已导出")}
            />
          )}
          {activeNav === "companies" && <CompaniesView companies={companies} jobs={jobs} onOpenJob={openJob} onDelete={deleteCompany} />}
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
          {activeNav === "trash" && (
            <TrashView
              trashedJobs={trashedJobs}
              trashedCompanies={trashedCompanies}
              onRefresh={async () => { await loadAll(); await reloadTrash(); }}
              onNotify={notify}
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
          onDelete={deleteJob}
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
                  待筛 <b>{wechatResult.raw_config?.pending ?? 0}</b> 个（去「聊天」勾选入库）
                  {!!wechatResult.updated_count && <> / 已在池刷新 <b>{wechatResult.updated_count}</b> 个</>}。
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

export default App;
