import { Download, X } from "lucide-react";
import { useEscapeClose } from "../hooks/useEscapeClose";

export function ExportCenterModal({
  onClose,
  onExport,
  busy
}: {
  onClose: () => void;
  onExport: (path: string, fallbackName: string, successMessage: string) => Promise<void>;
  busy: boolean;
}) {
  useEscapeClose(true, onClose);
  const items = [
    { title: "岗位池", detail: "导出当前岗位、状态、分数、来源和链接（CSV，可用 Excel 打开）。", path: "/api/exports/jobs?format=csv", filename: "jobs.csv", message: "岗位池已导出" },
    { title: "完整归档", detail: "导出 JSON 备份，包含画像、岗位、评分、准备、待办、复盘和事件，可迁移留档。", path: "/api/exports/archive?format=json", filename: "archive.json", message: "完整归档已导出" }
  ];
  return (
    <div className="modal-backdrop">
      <div className="modal export-modal" role="dialog" aria-modal="true" aria-labelledby="export-center-title">
        <div className="modal-head">
          <div>
            <h2 id="export-center-title">导出中心</h2>
            <p className="muted">默认只导出本机数据，不上传云端，不包含环境变量和密钥。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="export-grid">
          {items.map((item) => (
            <article key={item.title} className="export-card">
              <div>
                <strong>{item.title}</strong>
                <p>{item.detail}</p>
              </div>
              <button
                type="button"
                className="small-action"
                onClick={() => onExport(item.path, item.filename, item.message)}
                disabled={busy}
              >
                <Download size={14} />
                导出
              </button>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
