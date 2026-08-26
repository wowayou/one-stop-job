import { AlertTriangle, ChevronDown, ChevronUp, Info, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, errorMessage, jsonBody } from "../api";
import { AboutPanel } from "../components/AboutPanel";
import { DiagnosticsPanel } from "../components/DiagnosticsPanel";
import { ProviderModal, type ProviderModalSaveValues } from "../components/ProviderModal";
import { DealbreakerChips } from "../components/DealbreakerChips";
import { hasAnyBusy, hasBusy, type BusyState } from "../hooks/useBusyState";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { DEFAULT_SCORING_WEIGHTS, GLOBAL_BUSY_KEYS } from "../lib/constants";
import {
  asConfigArray,
  asConfigMap,
  booleanValue,
  linesValue,
  numberValue,
  runCountsText,
  setConfigValue,
  splitLines,
  stringValue
} from "../lib/format";
import type { AiStatus, AppConfig, AutomationStatus, JobSourceStatus, NoticeKind, SourceRun, UserProfile } from "../types";

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
  ["advanced", "高级"],
  ["diagnostics", "诊断"],
  ["about", "关于"]
] as const;

type ConfigSection = (typeof configSections)[number][0];

export function ConfigView({
  initialSection,
  sources,
  runs,
  busy,
  profile,
  onNotify,
  onAiStatus,
  onCollectSource,
  onUpdateProfile,
  onUpdateWeights,
  onProfilePatched,
  automation,
  onAutomationChanged
}: {
  /** 直接停在某个分区（App 里点侧栏「有新版本」→「关于」）。靠外层换 key 重新挂载生效。 */
  initialSection?: ConfigSection;
  sources: JobSourceStatus[];
  runs: SourceRun[];
  busy: BusyState;
  profile: UserProfile | null;
  onNotify: (kind: NoticeKind, message: string, details?: string[]) => void;
  onAiStatus: (status: AiStatus) => void;
  onCollectSource: (sourceKey: string, label: string, zeroFallback: string) => Promise<void>;
  onUpdateProfile: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  onUpdateWeights: (weights: Record<string, number>) => Promise<void>;
  onProfilePatched: (profile: UserProfile) => void;
  automation: AutomationStatus | null;
  onAutomationChanged: (status: AutomationStatus) => void;
}) {
  const [payload, setPayload] = useState<AppConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [automationBusy, setAutomationBusy] = useState(false);
  const [activeSection, setActiveSection] = useState<ConfigSection>(initialSection ?? "status");
  const [envExampleOpen, setEnvExampleOpen] = useState(false);
  useEscapeClose(envExampleOpen, () => setEnvExampleOpen(false));
  // key 本身绝不进 React state 以外的任何地方（不落 config 草稿、不进 URL/日志）；
  // provider 卡在主视图里只读，编辑/新增都要经过 ProviderModal 的本地表单状态，
  // 界面全程不回显任何已保存的 key（只显示「已配置/未配置」徽标，来自 aiStatus.provider_keys）。
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  // null = 弹窗关闭；{mode:"add"} = 新增；{mode:"edit", index} = 编辑第 index 张卡。
  const [providerModal, setProviderModal] = useState<{ mode: "add" } | { mode: "edit"; index: number } | null>(null);
  const [providerModalSaving, setProviderModalSaving] = useState(false);
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

  useEffect(() => {
    let active = true;
    api<AiStatus>("/api/ai/status")
      .then((data) => {
        if (active) setAiStatus(data);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  async function refreshAiStatus() {
    try {
      const latest = await api<AiStatus>("/api/ai/status");
      setAiStatus(latest);
      onAiStatus(latest);
    } catch {
      // 状态刷新失败不影响主流程；下次手动切换 tab 或保存时还会再拉一次。
    }
  }

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
      await refreshAiStatus();
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
  const aiProviders = asConfigArray(ai.providers);
  const general = asConfigMap(config.general);
  const sourceByKey = (key: string) => sources.find((source) => source.key === key);
  const bossSource = sourceByKey("boss");
  const zhilianStatus = sourceByKey("zhilian");
  const bebeeStatus = sourceByKey("bebee");
  const wechatLabel = stringValue(wechat.source_label, "公众号");
  const latestWechatRun = runs.find((run) => run.source === wechatLabel);

  // ai.providers 是有序 provider 列表，靠 *_env 字段名指名去哪个 .env 变量读真实密钥
  // （services/ai.py::_normalize_provider）；这里只编辑数组结构，不涉及密钥本身。
  function updateProviders(next: Record<string, unknown>[]) {
    updateConfig(["ai", "providers"], next);
  }

  // 每张 provider 卡的 key 归属靠一个稳定、随机生成的 env 变量名（不是数组下标——下标会因
  // 增删/重排错位而错位），生成后写进这张卡的 api_key_env，此后终身不变，对用户只读小字展示。
  function generateProviderEnvName(): string {
    const bytes = new Uint8Array(4);
    if (typeof crypto !== "undefined" && "getRandomValues" in crypto) {
      crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    }
    const suffix = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
    return `AI_PROVIDER_KEY_${suffix}`;
  }

  // 把当前草稿里的整个 ai 配置段单独 PUT 给后端（不牵动其它 tab 未保存的编辑）：
  // provider 卡的增/删/改/排序和「保存 Key」都需要保证 config.yaml 里的 provider 列表
  // 立即落盘，不依赖页面顶部那个「保存配置」大按钮——摘要卡是只读展示，任何结构变化都是
  // 一次独立、即时生效的动作。PUT /api/config 按 config.yaml 现状 + 传入的顶层段合并，
  // 不会波及未提交的其它 tab 草稿；成功后只把返回的 ai 段合回本地草稿。
  async function persistAiSection(nextAi: Record<string, unknown>) {
    const saved = await api<AppConfig>("/api/config", { method: "PUT", ...jsonBody({ config: { ai: nextAi } }) });
    setPayload((current) => (current ? { ...current, config: setConfigValue(current.config, ["ai"], asConfigMap(saved.config.ai)) } : current));
    return asConfigMap(saved.config.ai);
  }

  function openAddProviderModal() {
    setProviderModal({ mode: "add" });
  }

  function openEditProviderModal(index: number) {
    setProviderModal({ mode: "edit", index });
  }

  function closeProviderModal() {
    if (providerModalSaving) return;
    setProviderModal(null);
  }

  async function removeProvider(index: number) {
    const next = aiProviders.filter((_, i) => i !== index);
    try {
      await persistAiSection({ ...ai, providers: next });
      onNotify("success", "已删除该 Provider。");
      await refreshAiStatus();
    } catch (err) {
      onNotify("error", errorMessage(err, "删除失败"));
    }
  }

  async function moveProvider(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= aiProviders.length) return;
    const next = [...aiProviders];
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    try {
      await persistAiSection({ ...ai, providers: next });
    } catch (err) {
      onNotify("error", errorMessage(err, "调整顺序失败"));
    }
  }

  // ProviderModal 提交时统一处理：新增先生成稳定 env 名再入列，编辑只改 label/base_url/model；
  // 落盘 provider 结构后，如果填了 key（含勾选的「同时写入其它 provider」）再顺序写 .env——
  // 保证「.env 里的 key」和「config.yaml 里引用它的 provider」不会出现只写一半的情况。
  async function handleProviderModalSave(values: ProviderModalSaveValues) {
    if (!providerModal) return;
    setProviderModalSaving(true);
    try {
      let nextProviders = aiProviders;
      let targetIndex: number;
      if (providerModal.mode === "edit") {
        targetIndex = providerModal.index;
        nextProviders = nextProviders.map((provider, i) =>
          i === targetIndex ? { ...provider, label: values.label, base_url: values.baseUrl, model: values.model } : provider
        );
      } else {
        nextProviders = [...nextProviders, { label: values.label, api_key_env: generateProviderEnvName(), base_url: values.baseUrl, model: values.model }];
        targetIndex = nextProviders.length - 1;
      }

      const cardIndexes = values.key ? [targetIndex, ...values.applyKeyTo] : [];
      const envNames: string[] = [];
      for (const cardIndex of cardIndexes) {
        let envName = stringValue(nextProviders[cardIndex]?.api_key_env).trim();
        if (!envName) {
          envName = generateProviderEnvName();
          nextProviders = nextProviders.map((provider, i) => (i === cardIndex ? { ...provider, api_key_env: envName } : provider));
        }
        envNames.push(envName);
      }

      await persistAiSection({ ...ai, providers: nextProviders });

      for (const envName of envNames) {
        // eslint-disable-next-line no-await-in-loop -- 顺序写多个 env，避免并发写 .env 互相覆盖
        await api<{ ok: boolean; env_name: string }>("/api/ai/credentials", { method: "POST", ...jsonBody({ env_name: envName, value: values.key }) });
      }

      onNotify("success", envNames.length ? `Provider 已保存，Key 已写入 .env（${envNames.join(" / ")}）。` : "Provider 已保存。");
      await refreshAiStatus();
      setProviderModal(null);
    } catch (err) {
      onNotify("error", errorMessage(err, "保存 Provider 失败"));
    } finally {
      setProviderModalSaving(false);
    }
  }

  function sourceStatus(source?: JobSourceStatus) {
    if (!source) return <span className="status not_configured">未配置</span>;
    return <span className={`status ${source.status}`}>{source.enabled ? source.status : "disabled"}</span>;
  }

  async function updateAutomation(mode: "manual" | "autopilot", reachLevel = automation?.reach_level ?? "core") {
    setAutomationBusy(true);
    try {
      const next = await api<AutomationStatus & { rescored?: { jobs: number; candidates: number } }>("/api/automation/settings", {
        method: "PUT",
        ...jsonBody({ mode, reach_level: reachLevel, rescore_existing: true })
      });
      onAutomationChanged(next);
      onNotify(
        "success",
        mode === "autopilot" ? "自动驾驶已开启；每天只生成本地待确认队列，不会自动投递。" : "自动化已停止。",
        next.rescored ? [`已重评岗位 ${next.rescored.jobs} 个、待筛候选 ${next.rescored.candidates} 个。`] : undefined
      );
    } catch (err) {
      onNotify("error", errorMessage(err, "自动驾驶配置更新失败"));
    } finally {
      setAutomationBusy(false);
    }
  }

  async function scanNow() {
    setAutomationBusy(true);
    try {
      const run = await api<SourceRun>("/api/automation/scan", { method: "POST" });
      onNotify("success", `扫描完成：发现 ${run.fetched_count} 条，待筛 ${run.raw_config?.pending ?? 0} 条。`);
      const status = await api<AutomationStatus>("/api/automation/status");
      onAutomationChanged(status);
    } catch (err) {
      onNotify("error", errorMessage(err, "立即扫描失败"));
    } finally {
      setAutomationBusy(false);
    }
  }

  function latestRunText(run?: SourceRun | null) {
    if (!run) return "未运行";
    return `${run.status} · ${runCountsText(run)}`;
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

  if (activeSection === "diagnostics") {
    return (
      <section className="content-panel config-panel">
        <div className="config-layout">
          <div className="config-head">
            <div>
              <h2>诊断</h2>
              <p>出问题时先看这里：.env 与 config.yaml 分开显示，另有备份、脱敏日志和缓存重置</p>
            </div>
          </div>
          {tabs}
          <div className="config-scroll">
            <DiagnosticsPanel onNotify={onNotify} />
          </div>
        </div>
      </section>
    );
  }

  if (activeSection === "about") {
    return (
      <section className="content-panel config-panel">
        <div className="config-layout">
          <div className="config-head">
            <div>
              <h2>关于</h2>
              <p>版本、升级检查与下载入口；只发现新版本，不会自动下载或安装</p>
            </div>
          </div>
          {tabs}
          <div className="config-scroll">
            <AboutPanel onNotify={onNotify} />
          </div>
        </div>
      </section>
    );
  }

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
                  <DealbreakerChips
                    profile={profile}
                    onUpdated={onProfilePatched}
                    onNotify={(kind, message) => onNotify(kind, message)}
                  />
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
              <fieldset className="automation-card">
                <legend>自动驾驶</legend>
                <div className="automation-control-row">
                  <div>
                    <strong>{automation?.mode === "autopilot" ? "已开启" : "手动"}</strong>
                    <p>{automation?.safe_boundary ?? "只生成本地候选与材料，不自动投递。"}</p>
                  </div>
                  <div className="segmented" aria-label="自动驾驶模式">
                    <button type="button" className={automation?.mode !== "autopilot" ? "active" : ""} disabled={automationBusy} onClick={() => updateAutomation("manual")}>关</button>
                    <button type="button" className={automation?.mode === "autopilot" ? "active" : ""} disabled={automationBusy} onClick={() => updateAutomation("autopilot")}>开</button>
                  </div>
                </div>
                <div>
                  <span className="automation-label">求职面</span>
                  <div className="segmented reach-segmented" aria-label="求职相邻度">
                    {(["core", "adjacent", "exploratory"] as const).map((level) => (
                      <button
                        key={level}
                        type="button"
                        className={(automation?.reach_level ?? "core") === level ? "active" : ""}
                        disabled={automationBusy}
                        onClick={() => updateAutomation(automation?.mode ?? "manual", level)}
                      >
                        {level === "core" ? "核心" : level === "adjacent" ? "相邻" : "探索"}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="automation-metrics">
                  <span>最近发现 <strong>{automation?.latest_counts.found ?? 0}</strong></span>
                  <span>硬拦截 <strong>{automation?.latest_counts.hard_blocked ?? 0}</strong></span>
                  <span>待确认 <strong>{automation?.latest_counts.pending ?? 0}</strong></span>
                  <span>材料包 <strong>{automation?.latest_counts.materials_prepared ?? 0}</strong></span>
                </div>
                <div className="automation-actions">
                  <button type="button" className="small-action" disabled={automationBusy} onClick={scanNow}>
                    {automationBusy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
                    立即扫描
                  </button>
                  <button type="button" className="danger-action" disabled={automationBusy || automation?.mode !== "autopilot"} onClick={() => updateAutomation("manual")}>
                    停止自动化
                  </button>
                </div>
              </fieldset>
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
                {payload.env.openai_api_key_configured && (
                  <div className="config-status-grid compact">
                    <div>
                      <span>OPENAI_API_KEY（无 provider 卡时的兜底）</span>
                      <strong>已配置</strong>
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
                )}
                <button type="button" className="small-action config-example-button" onClick={() => setEnvExampleOpen(true)}>
                  <Info size={14} />
                  配置示例
                </button>
              </fieldset>

              <fieldset>
                <legend>AI Provider（按顺序容错）</legend>
                <p className="muted">
                  一张卡就是一个 provider：名称、Key、Base URL、Model 都属于这张卡，Key 只属于这张卡。
                  Key 只写入本机 <code>.env</code>，不进 config.yaml / 数据库 / git，界面全程不回显已保存的 key（只显示
                  「已配置/未配置」）。列表按顺序尝试，前一个失败退避重试后换下一个。
                </p>
                {aiProviders.length === 0 && (
                  <p className="muted">
                    还没有 provider 卡；AI 兜底沿用单一 <code>OPENAI_API_KEY</code>/<code>OPENAI_BASE_URL</code>/
                    <code>OPENAI_MODEL</code> 环境变量（见上方「配置示例」）。点「添加 Provider」开始按卡片管理。
                  </p>
                )}
                {aiProviders.length > 0 && (
                  <div className="provider-summary-list">
                    {aiProviders.map((provider, index) => {
                      const envName = stringValue(provider.api_key_env).trim();
                      const hasKey = Boolean(envName && aiStatus?.provider_keys?.[envName]);
                      const baseUrl = stringValue(provider.base_url);
                      const baseUrlText = baseUrl.length > 42 ? `${baseUrl.slice(0, 42)}…` : baseUrl;
                      return (
                        <div className="provider-summary-card" key={index}>
                          <div className="provider-summary-main">
                            <strong>{stringValue(provider.label) || `Provider #${index + 1}`}</strong>
                            <span className={`status ${hasKey ? "ok" : "disabled"}`}>{hasKey ? "Key 已配置" : "Key 未配置"}</span>
                          </div>
                          <div className="provider-summary-meta">
                            <span>{stringValue(provider.model) || "未设置 model"}</span>
                            <span title={baseUrl}>{baseUrlText || "未设置 base_url"}</span>
                          </div>
                          <div className="provider-summary-actions">
                            <button
                              type="button"
                              className="icon-button compact"
                              title="上移"
                              disabled={index === 0}
                              onClick={() => moveProvider(index, -1)}
                            >
                              <ChevronUp size={14} />
                            </button>
                            <button
                              type="button"
                              className="icon-button compact"
                              title="下移"
                              disabled={index === aiProviders.length - 1}
                              onClick={() => moveProvider(index, 1)}
                            >
                              <ChevronDown size={14} />
                            </button>
                            <button type="button" className="small-action" onClick={() => openEditProviderModal(index)}>
                              <Pencil size={14} />
                              编辑
                            </button>
                            <button type="button" className="small-action" onClick={() => removeProvider(index)}>
                              <Trash2 size={14} />
                              删除
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                <button type="button" className="small-action" onClick={openAddProviderModal}>
                  <Plus size={14} />
                  添加 Provider
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
                <p className="muted">密钥只写本机 `.env`，本页不会显示、保存或写回已配置的 Key。</p>
              </div>
              <button type="button" className="icon-button" onClick={() => setEnvExampleOpen(false)} title="关闭">
                <X size={18} />
              </button>
            </div>
            <div className="modal-notes">
              <p>
                <strong>两种设 Key 方式：</strong>① 推荐——在「AI Provider」区添加/编辑 Provider 卡时直接填 Key，保存后写入本机{" "}
                <code>.env</code>，<strong>无需重启、当前进程即时生效</strong>，重新「测试连接」就能验证；② 手动——直接编辑项目根目录的{" "}
                <code>.env</code> 文件，之后重新「测试连接」或重启进程使其生效。
              </p>
              <p>
                <code>config.yaml</code> 只保存 <code>ai.enabled</code> 和 provider 的 <code>label</code>/<code>api_key_env</code>/
                <code>base_url</code>/<code>model</code>（非密钥配置），<strong>从不存、不显示、不写回 Key 本身</strong>。
              </p>
              <p>
                <strong>多 provider 容错：</strong>「AI Provider」卡片按列表顺序尝试，某个 provider 调用失败会先退避重试几次，仍失败才换下一个；全部失败才回落既有的规则/模板降级。
              </p>
            </div>
            <p className="muted">国内可用示例（阿里百炼 Qwen，兼容 OpenAI 协议）：</p>
            <pre className="env-snippet">{`# .env（手动方式二；方式一由 Provider 卡自动写入同一个变量名）
DASHSCOPE_API_KEY=sk-...

# 添加 Provider 卡时填：
# Base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
# Model（视觉/截图分析）: qwen-vl-max
# Model（纯文本）: qwen-plus`}</pre>
            <div className="modal-notes">
              <p>
                不配置任何 Provider 卡时，AI 兜底沿用单一 <code>OPENAI_API_KEY</code>/<code>OPENAI_BASE_URL</code>/
                <code>OPENAI_MODEL</code> 环境变量（当前：{payload.env.openai_api_key_configured ? "已配置" : "未配置"} ·{" "}
                {payload.env.openai_model}）。
              </p>
              <p>
                部署方式：日常用单进程模式（<code>scripts/app.sh</code>），Key/配置改动即时生效或 <code>scripts/app.sh update</code>{" "}
                后生效；Docker 是备用方案，那种部署下改 <code>.env</code> 才需要重启/重建容器。
              </p>
            </div>
          </div>
        </div>
      )}

      {providerModal &&
        (() => {
          const editing = providerModal.mode === "edit" ? aiProviders[providerModal.index] : undefined;
          const editingEnvName = editing ? stringValue(editing.api_key_env).trim() : "";
          const hasKey = Boolean(editingEnvName && aiStatus?.provider_keys?.[editingEnvName]);
          const otherProviders = aiProviders
            .map((provider, i) => ({ index: i, label: stringValue(provider.label) || `Provider #${i + 1}` }))
            .filter(({ index }) => providerModal.mode !== "edit" || index !== providerModal.index);
          return (
            <ProviderModal
              mode={providerModal.mode}
              initialLabel={stringValue(editing?.label)}
              initialBaseUrl={stringValue(editing?.base_url)}
              initialModel={stringValue(editing?.model)}
              hasKey={hasKey}
              otherProviders={otherProviders}
              saving={providerModalSaving}
              onClose={closeProviderModal}
              onSave={handleProviderModalSave}
            />
          );
        })()}
    </section>
  );
}
