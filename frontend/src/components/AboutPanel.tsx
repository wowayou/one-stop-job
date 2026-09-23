import { AlertCircle, CheckCircle2, Download, ExternalLink, Heart, Info, Loader2, RefreshCw, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorMessage, openExternal } from "../api";
import type { AiUsageSummary, NoticeKind, UpdateCheckResult } from "../types";

/** 设置 → 关于：当前版本、手动检查更新、状态与下载入口。
 *
 * 边界（对应 P0，刻意不越线）：**只发现，不安装**。这里没有任何下载/解包/重启逻辑，
 * 「下载新版本」是把系统浏览器指向 Release 资产地址，安装仍由用户自己完成。应用内
 * 一键更新需要代码签名与 updater 公钥，属于后续阶段。
 *
 * 平台判定和安装包匹配都在后端（同一台机器上，`sys.platform` 比 webview 里猜 UA 可靠），
 * 这里只渲染后端给的结果；`status` 的五种取值各有独立文案，尤其 `offline` 绝不能显示成
 * 「已是最新」。
 */

// 自愿赞助入口：统一指向 eigentime.org/support（而不是直接指向收款平台），方便以后换/增平台只改那一处。
// from=one-stop-job：已在 /support 的 allowlist 里单独登记本项目，用专属值便于归因（未入列会被归为 other）。
// 本地优先：不接任何支付/埋点/后端，仅用系统浏览器打开外部页。
const SUPPORT_URL = "https://eigentime.org/support?from=one-stop-job";

const USAGE_PURPOSE_LABELS: Record<string, string> = {
  extract: "材料抽取",
  prep: "面试准备",
  decision: "决策分析",
  probe: "连接测试",
  other: "其他"
};

const STATUS_META: Record<UpdateCheckResult["status"], { label: string; tone: "ok" | "info" | "warn" | "fail" }> = {
  update_available: { label: "有新版本", tone: "info" },
  latest: { label: "已是最新", tone: "ok" },
  offline: { label: "无法连接更新服务", tone: "warn" },
  error: { label: "检查失败", tone: "fail" },
  disabled: { label: "已关闭升级检查", tone: "warn" }
};

function StatusIcon({ status }: { status: UpdateCheckResult["status"] }) {
  if (status === "latest") return <CheckCircle2 size={16} />;
  if (status === "update_available") return <Download size={16} />;
  if (status === "offline") return <WifiOff size={16} />;
  if (status === "disabled") return <Info size={16} />;
  return <AlertCircle size={16} />;
}

function formatCheckedAt(seconds: number | null | undefined) {
  if (!seconds) return "尚未检查";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "尚未检查";
  return date.toLocaleString();
}

function formatSize(bytes: number | null | undefined) {
  if (!bytes || bytes <= 0) return null;
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function AboutPanel({ onNotify }: { onNotify: (kind: NoticeKind, message: string, details?: string[]) => void }) {
  const [result, setResult] = useState<UpdateCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [usage, setUsage] = useState<AiUsageSummary | null>(null);

  async function check(force: boolean) {
    setChecking(true);
    setFailure(null);
    try {
      const data = await api<UpdateCheckResult>(`/api/updates/check${force ? "?force=true" : ""}`);
      setResult(data);
      if (force) {
        onNotify(data.status === "update_available" ? "info" : data.status === "latest" ? "success" : "warning", data.message);
      }
    } catch (err) {
      // 端点本身不可达（后端没起来）与"检查到了但连不上 GitHub"是两回事，分开显示。
      setFailure(errorMessage(err, "检查更新失败"));
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    let active = true;
    api<UpdateCheckResult>("/api/updates/check")
      .then((data) => {
        if (active) setResult(data);
      })
      .catch((err) => {
        if (active) setFailure(errorMessage(err, "检查更新失败"));
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    api<AiUsageSummary>("/api/ai/usage")
      .then((data) => {
        if (active) setUsage(data);
      })
      .catch(() => {
        /* 用量只是参考信息，拿不到就不显示，不打扰。 */
      });
    return () => {
      active = false;
    };
  }, []);

  async function open(url: string | null | undefined, label: string) {
    if (!url) return;
    const ok = await openExternal(url);
    if (!ok) onNotify("warning", `无法自动打开${label}，请手动复制链接：${url}`);
  }

  const meta = result ? STATUS_META[result.status] : null;
  const downloadSize = formatSize(result?.download?.size);

  return (
    <div className="config-section-stack">
      <fieldset className="about-card">
        <legend>关于</legend>
        <div className="about-version-row">
          <div>
            <span>当前版本</span>
            <strong>v{result?.current_version ?? "—"}</strong>
          </div>
          <div>
            <span>本机平台</span>
            <strong>{result?.platform.label ?? "—"}</strong>
          </div>
          <div>
            <span>最近检查</span>
            <strong>{formatCheckedAt(result?.checked_at)}</strong>
          </div>
        </div>

        {failure && (
          <div className="config-alert warning" role="alert">
            <AlertCircle size={16} />
            <span>{failure}</span>
          </div>
        )}

        {meta && result && (
          <div className={`about-status ${meta.tone}`} aria-live="polite">
            <StatusIcon status={result.status} />
            <div>
              <strong>{meta.label}</strong>
              <span>{result.message}</span>
              {result.cached && result.status !== "disabled" && (
                <small>结果来自本地缓存（默认 6 小时内不重复请求），点「检查更新」可立即重查。</small>
              )}
            </div>
          </div>
        )}

        <div className="about-actions">
          <button type="button" className="primary-action" disabled={checking} onClick={() => check(true)}>
            {checking ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
            {checking ? "检查中…" : "检查更新"}
          </button>
          {result?.release_url && (
            <button type="button" className="small-action" onClick={() => open(result.release_url, "发布页")}>
              <ExternalLink size={15} />
              打开发布页
            </button>
          )}
          {result?.release_notes && (
            <button type="button" className="small-action" onClick={() => setNotesOpen((value) => !value)}>
              <Info size={15} />
              {notesOpen ? "收起发布说明" : "查看发布说明"}
            </button>
          )}
        </div>

        {result?.status === "update_available" && (
          <div className="about-download">
            <div className="about-download-head">
              <strong>v{result.latest_version}</strong>
              {result.published_at && <small>{new Date(result.published_at).toLocaleDateString()}</small>}
            </div>
            {result.download?.url ? (
              <>
                <button type="button" className="primary-action" onClick={() => open(result.download?.url, "下载页")}>
                  <Download size={15} />
                  下载 {result.platform.label} 安装包
                </button>
                <small className="muted">
                  {result.download.name}
                  {downloadSize ? ` · ${downloadSize}` : ""}
                </small>
              </>
            ) : (
              <small className="muted">
                这次发布里没有匹配 {result.platform.label} 的安装包，请到发布页手动挑选。
              </small>
            )}
            {result.checksum_url && (
              <button type="button" className="small-action" onClick={() => open(result.checksum_url, "校验文件")}>
                <ExternalLink size={15} />
                SHA-256 校验文件
              </button>
            )}
            <p className="about-note">
              下载后需要你自己确认并安装，应用不会自动下载或替换自己。升级不会改动 <code>.env</code>、
              <code>config.yaml</code>、本机 SQLite 和个人上下文仓库。
            </p>
          </div>
        )}

        {notesOpen && result?.release_notes && <pre className="about-notes">{result.release_notes}</pre>}

        <p className="about-note muted">
          升级检查只向 <code>{result?.repo ?? "GitHub"}</code> 的公开 Releases 接口发一次只读请求，
          不携带任何本地数据；在 <code>config.yaml</code> 的 <code>updates.enabled</code> 置 false 即完全关闭。
        </p>
      </fieldset>

      <fieldset className="about-card">
        <legend>支持创作者</legend>
        <p className="about-note">
          job-one-stop 由个人开发者 Eigentime 维护——平时也记录 AI、SEO、建站与工具实践，
          并做一些自己在用的免费 / 开源小项目。如果它帮到了你，可以自愿支持一下。
          支持完全出于自愿，不对应任何额外功能，也不影响免费使用。
        </p>
        <div className="about-actions">
          <button type="button" className="primary-action" onClick={() => open(SUPPORT_URL, "支持页")}>
            <Heart size={15} />
            支持创作者
          </button>
        </div>
        <p className="about-note muted">
          链接用系统浏览器打开 <code>eigentime.org/support</code>，赞助在外部页面完成；
          应用本身不接入任何支付、不收集赞助信息。
        </p>
      </fieldset>

      {usage && usage.calls > 0 && (
        <fieldset className="about-card">
          <legend>AI 用量</legend>
          <div className="about-version-row">
            <div>
              <span>累计调用</span>
              <strong>{usage.calls} 次</strong>
            </div>
            <div>
              <span>输入 tokens</span>
              <strong>{usage.prompt_tokens.toLocaleString()}</strong>
            </div>
            <div>
              <span>输出 tokens</span>
              <strong>{usage.completion_tokens.toLocaleString()}</strong>
            </div>
            <div>
              <span>合计 tokens</span>
              <strong>{usage.total_tokens.toLocaleString()}</strong>
            </div>
          </div>
          {usage.by_purpose.length > 0 && (
            <ul className="snapshot-change-list">
              {usage.by_purpose.map((row) => (
                <li key={row.purpose}>
                  <span className="snapshot-change-item">
                    {USAGE_PURPOSE_LABELS[row.purpose] ?? row.purpose}：{row.calls} 次 · {row.total_tokens.toLocaleString()} tokens
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="about-note muted">
            只在本机统计 token 计数，不存密钥、不存请求/响应内容。具体计费以各 provider 账单为准。
          </p>
        </fieldset>
      )}
    </div>
  );
}
