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
            <p className="muted">材料丢进聊天拿判断，确认入库后再按“岗位池、调研、准备、待办”推进，每天用冲刺包收口。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="guide-grid">
          <article className="guide-card guide-card-primary">
            <span>每日闭环</span>
            <ol>
              <li>在设置里校准个人画像、目标城市、薪资和排除项——建议和评分都以它为准。</li>
              <li>看到岗位就丢进聊天（JD 文本 / 截图 / 链接），拿到优先级、方向和下一步。</li>
              <li>值得推进的勾选「入库选中」；批量岗位再用宿主机采集、CSV、公众号或 beBee 补。</li>
              <li>打开高潜岗位，补公司证据、刷新评分并生成准备材料。</li>
              <li>生成今日求职冲刺包，把 Top 岗位转成待办。</li>
            </ol>
          </article>

          <article className="guide-card">
            <span>聊天怎么用</span>
            <p>
              识别出的候选<strong>默认不入库</strong>，每条会带一句按你的决策规则给出的初步建议。
              想追问某个候选，先在它上面点「问这个」，输入框上方会出现「针对 ① …」，这一条提问就锁定到那个岗位。
            </p>
          </article>

          <article className="guide-card">
            <span>手机上（可选）</span>
            <p>
              配好 Telegram 后，手机发链接或截图给自己的 bot：先收到「识别到 N 个候选」，随后单独一条建议。
              追问用 <code>?</code> 或 <code>/ask</code> 开头；多个候选时 <code>?2 你的问题</code> 指名问第几个。
              回复某条回执再发材料，会并进同一条线索。
            </p>
          </article>

          <article className="guide-card">
            <span>采集边界</span>
            <p>BOSS / 智联在宿主机运行 OpenCLI 后导入；公众号和 beBee 若返回 0 岗位，先看跳过原因，再补正文、HTML 或 Network JSON 样例。</p>
          </article>

          <article className="guide-card">
            <span>数据边界</span>
            <p>岗位、公司证据、评分和任务都保存在本机 SQLite；密钥只放环境变量或 .env。系统只生成材料，不自动投递、不自动发消息；Telegram 的回执、建议和回答只发给你本人。</p>
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
