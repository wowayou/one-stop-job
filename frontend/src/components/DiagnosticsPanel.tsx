import {
  AlertCircle,
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardCopy,
  FolderOpen,
  HelpCircle,
  Loader2,
  RefreshCw,
  RotateCcw,
  ScrollText
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, copyToClipboard, errorMessage, openLocalPath } from "../api";
import { LOCAL_CACHE_KEYS } from "../lib/constants";
import type {
  BackupResult,
  DeploymentDiagnostics,
  LogTailResult,
  NoticeKind,
  RuntimeDiagnostics
} from "../types";

/** 设置 → 诊断：出问题时第一个该打开的页面，外加四个失败恢复入口。
 *
 * 明确分区（P4.5）：`.env` 与 `config.yaml` 各占一块，好回答"我改的那一处到底生效没有"——
 * 这两者经常被混着记，密钥只在 .env、功能开关只在 config.yaml。
 *
 * 两条边界：`.env` 一侧只显示变量名与「已配置 / 未配置」，永远不显示值；「网络」一节只汇总
 * 已有信号（升级检查上次的结果、代理、Telegram），不会为了画绿点去连别人的服务器。
 */

type StatusTone = "ok" | "warning" | "error" | "unknown";

function ToneIcon({ tone }: { tone: StatusTone }) {
  if (tone === "ok") return <CheckCircle2 size={15} />;
  if (tone === "unknown") return <HelpCircle size={15} />;
  return <AlertCircle size={15} />;
}

function formatUptime(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} 小时 ${minutes % 60} 分钟` : `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function DiagnosticsPanel({ onNotify }: { onNotify: (kind: NoticeKind, message: string, details?: string[]) => void }) {
  const [runtime, setRuntime] = useState<RuntimeDiagnostics | null>(null);
  const [deployment, setDeployment] = useState<DeploymentDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [checksOpen, setChecksOpen] = useState(false);
  const [logPreview, setLogPreview] = useState<LogTailResult | null>(null);

  async function load() {
    setLoading(true);
    setFailure(null);
    // 两个端点分别兜底：部署自检挂了不该连带把运行时信息也藏起来。
    const [runtimeResult, deploymentResult] = await Promise.allSettled([
      api<RuntimeDiagnostics>("/api/diagnostics/runtime"),
      api<DeploymentDiagnostics>("/api/diagnostics/deployment")
    ]);
    if (runtimeResult.status === "fulfilled") setRuntime(runtimeResult.value);
    else setFailure(errorMessage(runtimeResult.reason, "诊断信息加载失败"));
    if (deploymentResult.status === "fulfilled") setDeployment(deploymentResult.value);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function openDataDir() {
    if (!runtime) return;
    const ok = await openLocalPath(runtime.data.path);
    if (ok) return;
    // 浏览器里没有打开本机目录的能力：退回复制路径，别假装点了没反应。
    const copied = await copyToClipboard(runtime.data.path);
    onNotify(
      copied ? "info" : "warning",
      copied ? `浏览器里无法直接打开目录，路径已复制：${runtime.data.path}` : `数据目录：${runtime.data.path}`
    );
  }

  async function runBackup() {
    setBusy("backup");
    try {
      const result = await api<BackupResult>("/api/diagnostics/backup", { method: "POST" });
      onNotify(
        result.ok ? "success" : "warning",
        result.ok ? `${result.message}（${formatBytes(result.size_bytes)}）` : result.message,
        result.restore_hint ? [result.restore_hint] : undefined
      );
      if (result.ok) await load();
    } catch (err) {
      onNotify("error", errorMessage(err, "备份失败"));
    } finally {
      setBusy(null);
    }
  }

  async function copyLogs() {
    setBusy("logs");
    try {
      const result = await api<LogTailResult>("/api/diagnostics/logs?lines=400");
      setLogPreview(result);
      if (!result.available) {
        onNotify("info", result.message);
        return;
      }
      const copied = await copyToClipboard(result.text);
      onNotify(copied ? "success" : "warning", copied ? `已复制脱敏日志（${result.lines} 行）。` : "复制失败，可在下方直接选中日志文本。");
    } catch (err) {
      onNotify("error", errorMessage(err, "读取日志失败"));
    } finally {
      setBusy(null);
    }
  }

  async function resetLocalCache() {
    if (!window.confirm("只清界面偏好与更新检查缓存（引导是否看过、侧栏折叠、当前聊天线索等），不会删除任何岗位、公司或聊天数据。继续？")) return;
    setBusy("reset");
    let cleared = 0;
    try {
      for (const key of LOCAL_CACHE_KEYS) {
        if (window.localStorage.getItem(key) !== null) cleared += 1;
        window.localStorage.removeItem(key);
      }
    } catch {
      // localStorage 不可用：后端缓存照样清。
    }
    try {
      await api("/api/updates/check?force=true");   // force 顺带把后端那份升级检查缓存刷掉
    } catch {
      // 离线时清缓存也算成功：本地那部分已经清掉了。
    }
    setBusy(null);
    onNotify("success", `已重置本地缓存（清掉 ${cleared} 项界面偏好），数据未受影响。`, [
      "刷新页面后会重新显示「开始使用」引导。"
    ]);
  }

  if (loading && !runtime) {
    return (
      <div className="config-section-stack">
        <p className="muted">正在读取诊断信息…</p>
      </div>
    );
  }

  return (
    <div className="config-section-stack">
      {failure && (
        <div className="config-alert warning" role="alert">
          <AlertCircle size={16} />
          <span>{failure}</span>
        </div>
      )}

      <div className="diag-toolbar">
        <button type="button" className="small-action" disabled={loading} onClick={() => void load()}>
          {loading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
          刷新
        </button>
        {runtime && <small className="muted">采集于 {new Date(runtime.generated_at).toLocaleString()}</small>}
      </div>

      {runtime && (
        <>
          <fieldset className="diag-card">
            <legend>当前后端进程</legend>
            <div className="config-status-grid compact">
              <div><span>应用版本</span><strong>v{runtime.version}</strong></div>
              <div><span>运行形态</span><strong>{runtime.process.tauri_mode ? "桌面端（Tauri）" : runtime.process.executable_frozen ? "打包二进制" : "开发/单进程"}</strong></div>
              <div><span>监听端口</span><strong>{runtime.process.port}{runtime.process.tauri_mode ? "（本次启动动态分配）" : ""}</strong></div>
              <div><span>进程 PID</span><strong>{runtime.process.pid}</strong></div>
              <div><span>已运行</span><strong>{formatUptime(runtime.process.uptime_seconds)}</strong></div>
              <div><span>Python</span><strong>{runtime.process.python}</strong></div>
            </div>
          </fieldset>

          <fieldset className="diag-card">
            <legend>当前 AI Provider</legend>
            <div className="config-status-grid compact">
              <div><span>状态</span><strong>{runtime.ai.available ? "可用" : runtime.ai.enabled_in_config ? "已启用但不可用" : "未启用"}</strong></div>
              <div><span>会先用</span><strong>{runtime.ai.provider_label}</strong></div>
              <div><span>模型</span><strong>{runtime.ai.model}</strong></div>
              <div><span>Key</span><strong>{runtime.ai.api_key_configured ? "已配置" : "未配置"}</strong></div>
              <div><span>Provider 卡</span><strong>{runtime.ai.provider_count || "未配置多卡"}</strong></div>
              <div><span>调用超时</span><strong>{runtime.ai.timeout_seconds}s</strong></div>
            </div>
            <p className="diag-note">Key 只显示「已配置 / 未配置」，界面与接口都不会回传值本身。</p>
          </fieldset>

          <fieldset className="diag-card">
            <legend>.env（密钥与环境变量）</legend>
            {runtime.env.map((group) => (
              <div key={group.group} className="diag-env-group">
                <span className="diag-env-label">{group.group}</span>
                <div className="diag-chip-row">
                  {group.vars.map((item) => (
                    <span key={item.name} className={item.configured ? "diag-chip on" : "diag-chip"}>
                      {item.name}
                      <small>{item.configured ? "已配置" : "未配置"}</small>
                    </span>
                  ))}
                </div>
              </div>
            ))}
            <p className="diag-note">只列变量名与是否有值。密钥一律只进 <code>.env</code>，不进 <code>config.yaml</code>。</p>
          </fieldset>

          <fieldset className="diag-card">
            <legend>config.yaml（功能配置）</legend>
            <div className="config-status-grid compact">
              <div><span>文件</span><strong>{runtime.config.exists ? "存在" : "不存在（用内置默认值）"}</strong></div>
              <div><span>解析</span><strong>{runtime.config.error ? "有错误" : "正常"}</strong></div>
            </div>
            {runtime.config.error && (
              <div className="config-alert warning" role="alert">
                <AlertCircle size={16} />
                <span>{runtime.config.error}</span>
              </div>
            )}
            <div className="diag-chip-row">
              {runtime.config.sections.map((section) => (
                <span key={section.name} className={section.present ? "diag-chip on" : "diag-chip"}>
                  {section.name}
                  <small>{section.present ? "已配置" : "缺省"}</small>
                </span>
              ))}
            </div>
            <p className="diag-note">路径：<code>{runtime.config.path}</code></p>
          </fieldset>

          <fieldset className="diag-card">
            <legend>网络连接状态</legend>
            {runtime.network.signals.map((signal) => (
              <div key={signal.name} className={`diag-signal ${signal.status}`}>
                <ToneIcon tone={signal.status} />
                <div>
                  <strong>{signal.name}</strong>
                  <span>{signal.detail}</span>
                  {signal.checked_at && <small>检查于 {new Date(signal.checked_at * 1000).toLocaleString()}</small>}
                </div>
              </div>
            ))}
            <p className="diag-note">{runtime.network.note}</p>
          </fieldset>
        </>
      )}

      {deployment && (
        <fieldset className="diag-card">
          <legend>部署自检</legend>
          <div className={`diag-signal ${deployment.status === "ok" ? "ok" : deployment.status === "error" ? "error" : "warning"}`}>
            <ToneIcon tone={deployment.status === "ok" ? "ok" : deployment.status === "error" ? "error" : "warning"} />
            <div>
              <strong>
                {deployment.status === "ok" ? "全部通过" : deployment.status === "error" ? "有阻断问题" : "有需要注意的项"}
              </strong>
              <span>
                {deployment.checks.filter((check) => check.status === "ok").length} 项通过 ·{" "}
                {deployment.checks.filter((check) => check.status === "warning").length} 项注意 ·{" "}
                {deployment.checks.filter((check) => check.status === "error").length} 项错误
              </span>
            </div>
          </div>
          <button type="button" className="small-action" onClick={() => setChecksOpen((value) => !value)}>
            {checksOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {checksOpen ? "收起明细" : "展开明细"}
          </button>
          {checksOpen && (
            <ul className="diag-check-list">
              {deployment.checks.map((check) => (
                <li key={check.name} className={check.status}>
                  <ToneIcon tone={check.status} />
                  <div>
                    <strong>{check.label ?? check.name}</strong>
                    <span>{check.message}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
      )}

      <fieldset className="diag-card">
        <legend>失败恢复</legend>
        <div className="diag-recovery-grid">
          <button type="button" className="small-action" onClick={() => void openDataDir()} disabled={!runtime}>
            <FolderOpen size={15} />
            打开数据目录
          </button>
          <button type="button" className="small-action" onClick={() => void runBackup()} disabled={busy === "backup"}>
            {busy === "backup" ? <Loader2 size={15} className="spin" /> : <Archive size={15} />}
            备份数据
          </button>
          <button type="button" className="small-action" onClick={() => void copyLogs()} disabled={busy === "logs"}>
            {busy === "logs" ? <Loader2 size={15} className="spin" /> : <ScrollText size={15} />}
            复制脱敏日志
          </button>
          <button type="button" className="small-action danger-text" onClick={() => void resetLocalCache()} disabled={busy === "reset"}>
            {busy === "reset" ? <Loader2 size={15} className="spin" /> : <RotateCcw size={15} />}
            重置本地缓存
          </button>
        </div>
        {runtime && (
          <div className="config-status-grid compact">
            <div><span>数据目录</span><strong>{runtime.data.writable ? "可写" : "不可写"}</strong></div>
            <div><span>已有备份</span><strong>{runtime.data.backup_count} 份</strong></div>
            <div><span>日志文件</span><strong>{runtime.data.log_exists ? "存在" : "无（桌面端不落盘）"}</strong></div>
          </div>
        )}
        <p className="diag-note">
          「备份数据」在线复制一份 SQLite 与聊天附件到 <code>data/backups/&lt;时间戳&gt;/</code>，
          只新建、不覆盖既有备份，也不动原库；口径与 <code>scripts/app.sh backup</code> 一致，两边可互相还原。
          「重置本地缓存」只清界面偏好与更新检查缓存，<strong>不删除任何岗位、公司或聊天数据</strong>。
        </p>
        {logPreview?.available && (
          <>
            <div className="diag-toolbar">
              <small className="muted">{logPreview.message}</small>
              <button type="button" className="small-action" onClick={() => setLogPreview(null)}>
                <ClipboardCopy size={14} />
                收起
              </button>
            </div>
            <pre className="diag-log">{logPreview.text}</pre>
          </>
        )}
      </fieldset>
    </div>
  );
}
