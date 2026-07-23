import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import type { Notice } from "../types";

export function NoticeBanner({ notice, onClose }: { notice: Notice; onClose: () => void }) {
  const Icon = {
    info: Info,
    success: CheckCircle2,
    warning: AlertTriangle,
    error: AlertCircle
  }[notice.kind];
  return (
    <div className={`notice-bar ${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
      <Icon size={18} />
      <div>
        <strong>{notice.message}</strong>
        {!!notice.details?.length && (
          <ul>
            {notice.details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" className="icon-button" onClick={onClose} title="关闭通知">
        <X size={16} />
      </button>
    </div>
  );
}
