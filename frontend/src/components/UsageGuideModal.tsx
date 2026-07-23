import { CheckCircle2, X } from "lucide-react";
import { useEscapeClose } from "../hooks/useEscapeClose";

export function UsageGuideModal({ onClose, onStartTour }: { onClose: () => void; onStartTour: () => void }) {
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
