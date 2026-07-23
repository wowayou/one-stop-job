import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { scoreClass } from "../lib/format";
import type { Job } from "../types";
import { ScoreBreakdown } from "./ScoreBreakdown";

// 表格评分芯片：点开一个轻量 popover 展示同一份分解，不用离开列表去开抽屉。
// popover 用 portal 挂到 body 上，用 fixed 定位——表格行在一个 overflow:auto 的滚动容器里，
// 普通 absolute 定位会被那层滚动裁掉；滚动/resize 时直接关闭而不是跟着重新定位，足够轻量。
export function ScoreChip({
  job,
  busy,
  onScoreJob
}: {
  job: Job;
  busy: BusyState;
  onScoreJob: (jobId: number) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const score = job.latest_score;
  const scoringBusy = hasBusy(busy, `score-${job.id}`);

  useEscapeClose(open, () => setOpen(false));

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setOpen(false);
    }
    function handleDismiss() {
      setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("scroll", handleDismiss, true);
    window.addEventListener("resize", handleDismiss);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("scroll", handleDismiss, true);
      window.removeEventListener("resize", handleDismiss);
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        className={`${scoreClass(score?.total)} score-chip-trigger`}
        title={score ? "点击查看评分分解" : "尚未评分，点击查看"}
        onClick={(event) => {
          event.stopPropagation();
          if (open) {
            setOpen(false);
            return;
          }
          const rect = triggerRef.current?.getBoundingClientRect();
          if (rect) {
            const width = 340;
            const left = Math.min(rect.left, window.innerWidth - width - 12);
            setPos({ top: rect.bottom + 6, left: Math.max(12, left) });
          }
          setOpen(true);
        }}
      >
        {score?.total ?? "-"}
      </button>
      {open && pos &&
        createPortal(
          <div
            ref={popoverRef}
            className="score-popover"
            style={{ top: pos.top, left: pos.left }}
            onClick={(event) => event.stopPropagation()}
          >
            {score ? (
              <ScoreBreakdown score={score} />
            ) : (
              <div className="score-popover-empty">
                <p className="muted">尚未评分</p>
                <button
                  type="button"
                  className="small-action"
                  disabled={scoringBusy}
                  onClick={() => {
                    setOpen(false);
                    void onScoreJob(job.id);
                  }}
                >
                  {scoringBusy ? "评分中…" : "去评分"}
                </button>
              </div>
            )}
          </div>,
          document.body
        )}
    </>
  );
}
