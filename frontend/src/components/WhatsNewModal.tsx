import { ShieldCheck, Sparkles, X } from "lucide-react";
import { useEscapeClose } from "../hooks/useEscapeClose";
import type { WhatsNew } from "../lib/whatsNew";

/** 升级后首次启动的「本版新增」说明，只弹一次。
 *
 * 第二段（配置保留确认）是刻意保留的：升级时用户最担心的不是"有什么新功能"，
 * 而是"我的画像/Key/看板会不会被动过"。把不会变的东西写清楚比一句"升级成功"有用。
 */
export function WhatsNewModal({ content, onClose }: { content: WhatsNew; onClose: () => void }) {
  useEscapeClose(true, onClose);

  return (
    <div className="modal-backdrop">
      <div className="modal whats-new-modal" role="dialog" aria-modal="true" aria-labelledby="whats-new-title">
        <div className="modal-head">
          <div>
            <h2 id="whats-new-title">已升级到 v{content.version}</h2>
            <p className="muted">{content.headline}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>

        <section aria-label="本版新增">
          <div className="whats-new-section-head">
            <Sparkles size={16} />
            <strong>本版新增</strong>
          </div>
          <ul className="whats-new-list">
            {content.highlights.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section aria-label="升级不会改动这些">
          <div className="whats-new-section-head">
            <ShieldCheck size={16} />
            <strong>升级不会改动这些</strong>
          </div>
          <ul className="whats-new-list preserved">
            {content.preserved.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <div className="modal-actions">
          <button type="button" className="primary-action" onClick={onClose}>
            知道了
          </button>
        </div>
      </div>
    </div>
  );
}
