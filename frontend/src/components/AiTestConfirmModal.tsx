import { useState } from "react";
import { AlertCircle, X } from "lucide-react";
import { useEscapeClose } from "../hooks/useEscapeClose";
import type { AiStatus } from "../types";

/** 「测试连接」前的确认弹窗：说清它会**真的**发一次请求。
 *
 * 加它的原因是这个按钮的名字听起来像本地自检，实际会调用 provider 接口并按对方规则计费。
 * 金额极小，但"未经说明就花钱"本身是要修的体验问题。勾选"下次不再提示"后由调用方
 * 记进 localStorage，直接发请求。
 */
export function AiTestConfirmModal({
  aiStatus,
  onCancel,
  onConfirm
}: {
  aiStatus: AiStatus | null;
  onCancel: () => void;
  onConfirm: (skipNextTime: boolean) => void;
}) {
  const [skipNextTime, setSkipNextTime] = useState(false);
  useEscapeClose(true, onCancel);

  return (
    <div className="modal-backdrop">
      <div className="modal ai-test-modal" role="dialog" aria-modal="true" aria-labelledby="ai-test-confirm-title">
        <div className="modal-head">
          <div>
            <h2 id="ai-test-confirm-title">测试 AI 连接</h2>
            <p className="muted">这会真的调用一次模型接口，用来区分「未配置 / 可用 / 调不通」。</p>
          </div>
          <button type="button" className="icon-button" onClick={onCancel} title="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="config-status-grid compact">
          <div>
            <span>将使用的 Provider</span>
            <strong>{aiStatus?.provider_label ?? "未配置"}</strong>
          </div>
          <div>
            <span>模型</span>
            <strong>{aiStatus?.model ?? "—"}</strong>
          </div>
          <div>
            <span>API Key</span>
            <strong>{aiStatus?.api_key_configured ? "已配置" : "未配置"}</strong>
          </div>
        </div>

        <div className="config-alert warning" role="note">
          <AlertCircle size={16} />
          <span>
            将发送一次最小测试请求（一句「回复 ok」），<strong>可能产生极低额 API 费用</strong>，
            按你所用 Provider 的计费规则结算。
          </span>
        </div>

        <ul className="modal-notes">
          <li>不发送任何岗位内容、个人画像或聊天记录。</li>
          <li>配了多个 Provider 时按顺序尝试，成功后会显示实际命中的是哪一张卡。</li>
          <li>API Key 只在本机 <code>.env</code> 里，界面和响应都只显示「已配置 / 未配置」。</li>
        </ul>

        <label className="checkbox-line">
          <input type="checkbox" checked={skipNextTime} onChange={(event) => setSkipNextTime(event.target.checked)} />
          <span>下次不再提示，直接测试</span>
        </label>

        <div className="modal-actions">
          <button type="button" className="small-action" onClick={onCancel}>
            取消
          </button>
          <button type="button" className="primary-action" onClick={() => onConfirm(skipNextTime)}>
            发送测试请求
          </button>
        </div>
      </div>
    </div>
  );
}
