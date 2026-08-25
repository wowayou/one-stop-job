import { CheckCircle2, Circle, Info, MessageSquareText, Search, Settings, ShieldCheck, X } from "lucide-react";
import { useEscapeClose } from "../hooks/useEscapeClose";
import type { AiStatus, AutomationStatus, JobSourceStatus, UserProfile } from "../types";

type UsageGuideProps = {
  profile: UserProfile | null;
  bossSource: JobSourceStatus | null;
  aiStatus: AiStatus | null;
  automation: AutomationStatus | null;
  onClose: () => void;
  onOpenSettings: () => void;
  onOpenChat: () => void;
  onStartTour: () => void;
};

function ReadinessItem({ ready, optional = false, title, detail }: { ready: boolean; optional?: boolean; title: string; detail: string }) {
  return (
    <div className="onboarding-readiness-item">
      {ready ? <CheckCircle2 size={18} /> : optional ? <Info size={18} /> : <Circle size={18} />}
      <div>
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
      <small className={ready ? "ready" : optional ? "optional" : "todo"}>{ready ? "已就绪" : optional ? "可选" : "待完成"}</small>
    </div>
  );
}

export function UsageGuideModal({ profile, bossSource, aiStatus, automation, onClose, onOpenSettings, onOpenChat, onStartTour }: UsageGuideProps) {
  useEscapeClose(true, onClose);
  const profileReady = Boolean(profile?.target_titles.trim() && profile?.target_cities.trim());
  const bossReady = Boolean(bossSource?.enabled && (bossSource.configured || bossSource.status === "host_import_required"));
  const aiReady = Boolean(aiStatus?.available);
  const autopilotEnabled = automation?.mode === "autopilot";
  const reachLabel = automation?.reach_level === "exploratory" ? "探索" : automation?.reach_level === "adjacent" ? "相邻" : "核心";

  return (
    <div className="modal-backdrop">
      <div className="modal usage-guide-modal" role="dialog" aria-modal="true" aria-labelledby="usage-guide-title">
        <div className="modal-head">
          <div>
            <h2 id="usage-guide-title">开始使用</h2>
            <p className="muted">先校准个人规则，手动跑通一轮，再决定是否开启每日自动扫描。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭"><X size={18} /></button>
        </div>

        <section className="onboarding-readiness" aria-label="启用检查">
          <div className="onboarding-section-head">
            <div><span>启用检查</span><strong>开始前确认这四项</strong></div>
            <button type="button" className="small-action" onClick={onOpenSettings}><Settings size={15} />打开设置</button>
          </div>
          <div className="onboarding-readiness-grid">
            <ReadinessItem ready={profileReady} title="个人画像" detail={profileReady ? `${profile?.target_titles} · ${profile?.target_cities}` : "填写目标岗位、城市、薪资和排除项"} />
            <ReadinessItem ready={bossReady} title="BOSS 采集" detail={bossReady ? "来源可运行；首次仍建议手动扫描" : bossSource?.message || "检查 OpenCLI 命令与登录态"} />
            <ReadinessItem ready={aiReady} optional title="AI 增强" detail={aiReady ? `${aiStatus?.model} 可用` : "未配置也能用，本地规则与模板会继续工作"} />
            <ReadinessItem ready={!autopilotEnabled} title="首轮模式" detail={autopilotEnabled ? `自动驾驶已开启 · ${reachLabel}` : `手动 · ${reachLabel}，适合先验收结果`} />
          </div>
        </section>

        <div className="guide-grid onboarding-flow-grid">
          <article className="guide-card guide-card-primary">
            <span>推荐第一轮</span>
            <ol>
              <li><strong>校准画像：</strong>目标岗位、城市、薪资底线、通勤和排除项决定评分与硬拦截。</li>
              <li><strong>保持“手动 + 核心”：</strong>点击顶部“立即扫描”，先验证搜索词、OpenCLI 登录态和筛选结果。</li>
              <li><strong>去聊天确认：</strong>打开最新“采集 · BOSS直聘”线索，展开候选，查看相邻度、分数、风险问题和材料包。</li>
              <li><strong>只入库值得推进的：</strong>勾选后点“入库选中”；未勾选、硬阻断和已排除岗位不会进入岗位池。</li>
              <li><strong>跑稳后再自动化：</strong>确认结果可信，再切到“相邻/探索”或开启自动驾驶每日扫描。</li>
            </ol>
          </article>
          <article className="guide-card"><span><Search size={14} /> 求职面怎么选</span><p><strong>核心</strong>只找主方向；<strong>相邻</strong>按 70/30 扩到内容、CMS、B2B 数字营销；<strong>探索</strong>按 50/30/20 加入实施、支持、客户成功和项目运营。地域、薪资及排除项始终不变。</p></article>
          <article className="guide-card"><span><ShieldCheck size={14} /> 自动驾驶边界</span><p>每天最多扫描一次，自动去重、分类、评分并准备本地材料，最后仍进入人工确认队列。它不会提交申请、不会私信招聘者，也不会移动岗位状态或看板。</p></article>
          <article className="guide-card"><span><MessageSquareText size={14} /> 候选在哪里</span><p>所有新采集岗位都先进入“聊天”里的采集线索。顶部“待确认”是当前 pending 候选数量；硬阻断默认折叠，评分缺失的候选会保留供人工判断。</p></article>
        </div>

        <div className="guide-actions onboarding-actions">
          <button type="button" className="small-action" onClick={onClose}>稍后再说</button>
          <button type="button" className="small-action" onClick={onOpenChat}><MessageSquareText size={16} />查看待确认</button>
          <button type="button" className="primary-action" onClick={onStartTour}><CheckCircle2 size={18} />开始界面导览</button>
        </div>
      </div>
    </div>
  );
}
