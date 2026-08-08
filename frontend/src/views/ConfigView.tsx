import { AlertTriangle, ChevronDown, ChevronUp, Info, Loader2, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, errorMessage, jsonBody } from "../api";
import { hasAnyBusy, hasBusy, type BusyState } from "../hooks/useBusyState";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { DEFAULT_SCORING_WEIGHTS, GLOBAL_BUSY_KEYS } from "../lib/constants";
import {
  asConfigArray,
  asConfigMap,
  booleanValue,
  linesValue,
  numberValue,
  setConfigValue,
  splitLines,
  stringValue
} from "../lib/format";
import type { AiStatus, AppConfig, JobSourceStatus, NoticeKind, SourceRun, UserProfile } from "../types";

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

export function ConfigView({
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
  // key 本身绝不进 React state 以外的任何地方（不落 config 草稿、不进 URL/日志）；
  // 提交成功后立即清空这两个输入框，界面全程不回显 key。
  const [aiKeyEnvName, setAiKeyEnvName] = useState("");
  const [aiKeyValue, setAiKeyValue] = useState("");
  const [aiKeySubmitting, setAiKeySubmitting] = useState(false);
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

  async function submitAiCredential(event: FormEvent) {
    event.preventDefault();
    const envName = aiKeyEnvName.trim();
    const value = aiKeyValue;
    if (!envName || !value) {
      onNotify("error", "请填写 env 变量名和 key。");
      return;
    }
    setAiKeySubmitting(true);
    try {
      const result = await api<{ ok: boolean; env_name: string }>(
        "/api/ai/credentials",
        { method: "POST", ...jsonBody({ env_name: envName, value }) }
      );
      setAiKeyValue("");
      setAiKeyEnvName("");
      onNotify("success", `已写入 .env · ${result.env_name}（重新测试连接即可生效）`);
      const latestAi = await api<AiStatus>("/api/ai/status");
      onAiStatus(latestAi);
    } catch (err) {
      onNotify("error", errorMessage(err, "写入 .env 失败"));
    } finally {
      setAiKeySubmitting(false);
    }
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

  function updateProviderField(index: number, field: string, value: string) {
    updateProviders(aiProviders.map((provider, i) => (i === index ? { ...provider, [field]: value } : provider)));
  }

  function addProvider() {
    updateProviders([...aiProviders, { api_key_env: "", base_url: "", model: "" }]);
  }

  function removeProvider(index: number) {
    updateProviders(aiProviders.filter((_, i) => i !== index));
  }

  function moveProvider(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= aiProviders.length) return;
    const next = [...aiProviders];
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    updateProviders(next);
  }

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

              <fieldset>
                <legend>设置 API Key（写入本机 .env）</legend>
                <p className="muted">
                  key 只写入本机 <code>.env</code>，不进 config.yaml / 数据库 / git，界面也不会显示它。
                </p>
                <form className="inline-fields" onSubmit={submitAiCredential}>
                  <label>
                    env 变量名
                    <input
                      placeholder={stringValue(aiProviders[0]?.api_key_env) || "DASHSCOPE_API_KEY"}
                      value={aiKeyEnvName}
                      onChange={(event) => setAiKeyEnvName(event.target.value.toUpperCase())}
                      autoComplete="off"
                    />
                  </label>
                  <label>
                    Key
                    <input
                      type="password"
                      placeholder="sk-..."
                      value={aiKeyValue}
                      onChange={(event) => setAiKeyValue(event.target.value)}
                      autoComplete="new-password"
                    />
                  </label>
                  <button type="submit" className="small-action" disabled={aiKeySubmitting}>
                    {aiKeySubmitting ? "写入中…" : "写入 .env"}
                  </button>
                </form>
              </fieldset>

              <fieldset>
                <legend>模型 Provider（按顺序容错）</legend>
                <div className="config-alert warning" role="note">
                  <AlertTriangle size={16} />
                  <span>
                    <strong>Key 不在这里填</strong>
                    ——这里只填 key 所在的 <code>.env</code> 变量名（<code>api_key_env</code>）；真实密钥请写进项目根目录{" "}
                    <code>.env</code>，例如 <code>DASHSCOPE_API_KEY=sk-...</code>。列表按顺序尝试，前一个失败退避重试后换下一个。
                  </span>
                </div>
                {aiProviders.length === 0 && (
                  <p className="muted">
                    未配置多 provider 列表；AI 兜底沿用单一 <code>OPENAI_API_KEY</code>/<code>OPENAI_BASE_URL</code>/
                    <code>OPENAI_MODEL</code> 环境变量。点「添加 provider」可切到多 provider 容错模式。
                  </p>
                )}
                {aiProviders.length > 0 && (
                  <div className="provider-list">
                    {aiProviders.map((provider, index) => (
                      <div className="provider-row" key={index}>
                        <div className="inline-fields">
                          <label>
                            api_key_env
                            <input
                              placeholder="DASHSCOPE_API_KEY"
                              value={stringValue(provider.api_key_env)}
                              onChange={(event) => updateProviderField(index, "api_key_env", event.target.value)}
                            />
                          </label>
                          <label>
                            base_url
                            <input
                              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                              value={stringValue(provider.base_url)}
                              onChange={(event) => updateProviderField(index, "base_url", event.target.value)}
                            />
                          </label>
                          <label>
                            model
                            <input
                              placeholder="qwen-vl-max"
                              value={stringValue(provider.model)}
                              onChange={(event) => updateProviderField(index, "model", event.target.value)}
                            />
                          </label>
                        </div>
                        <div className="provider-row-actions">
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
                          <button type="button" className="small-action" onClick={() => removeProvider(index)}>
                            <Trash2 size={14} />
                            删除
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <button type="button" className="small-action" onClick={addProvider}>
                  <Plus size={14} />
                  添加 provider
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
