import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { X } from "lucide-react";

// 轻量自研聚光灯引导：用一个带超大 box-shadow 的高亮框罩住目标元素，
// 旁边浮出说明气泡。无第三方依赖；目标缺失时降级为居中气泡，绝不卡死。

export type TourStep = {
  // 目标元素的 data-tour 属性值；省略则该步为居中说明（无高亮）。
  target?: string;
  title: string;
  body: string;
};

type Rect = { top: number; left: number; width: number; height: number };

const PADDING = 8; // 高亮框相对目标的留白
const POPUP_W = 320;
const POPUP_GAP = 14; // 气泡与高亮框的间距

function readRect(target?: string): Rect | null {
  if (!target) return null;
  const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null; // 不可见（被折叠/隐藏）
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

// 根据高亮框位置选一个不溢出视口的气泡坐标：优先放下方，放不下放上方，再不行居中。
function popupPosition(rect: Rect | null): { top: number; left: number; centered: boolean } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (!rect) {
    return { top: Math.max(24, vh / 2 - 120), left: Math.max(16, vw / 2 - POPUP_W / 2), centered: true };
  }
  let left = rect.left + rect.width / 2 - POPUP_W / 2;
  left = Math.min(Math.max(16, left), vw - POPUP_W - 16);
  const below = rect.top + rect.height + PADDING + POPUP_GAP;
  const spaceBelow = vh - below;
  if (spaceBelow > 180) return { top: below, left, centered: false };
  const aboveSpace = rect.top - PADDING - POPUP_GAP;
  if (aboveSpace > 180) return { top: Math.max(16, aboveSpace - 200), left, centered: false };
  return { top: Math.max(24, vh / 2 - 120), left: Math.max(16, vw / 2 - POPUP_W / 2), centered: true };
}

export default function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  const step = steps[index];
  const isFirst = index === 0;
  const isLast = index === steps.length - 1;

  const refresh = useCallback(() => {
    setRect(readRect(step?.target));
  }, [step?.target]);

  // 切步时把目标滚动进视口，再测量位置。
  useLayoutEffect(() => {
    if (step?.target) {
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
      el?.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
    refresh();
  }, [step?.target, refresh]);

  // 视口变化时跟随重算，保证高亮框贴合。
  useEffect(() => {
    window.addEventListener("resize", refresh);
    window.addEventListener("scroll", refresh, true);
    return () => {
      window.removeEventListener("resize", refresh);
      window.removeEventListener("scroll", refresh, true);
    };
  }, [refresh]);

  // 键盘：Esc 退出，左右方向键翻步。
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight" && !isLast) setIndex((i) => i + 1);
      else if (event.key === "ArrowLeft" && !isFirst) setIndex((i) => i - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isFirst, isLast, onClose]);

  if (!step) return null;
  const popup = popupPosition(rect);

  return (
    <div className="tour-layer" role="dialog" aria-modal="true" aria-label="使用引导">
      {rect ? (
        <div
          className="tour-spotlight"
          style={{
            top: rect.top - PADDING,
            left: rect.left - PADDING,
            width: rect.width + PADDING * 2,
            height: rect.height + PADDING * 2
          }}
        />
      ) : (
        // 无目标时用一层普通遮罩接管点击，避免误触下层界面。
        <div className="tour-backdrop" onClick={onClose} />
      )}

      <div
        className={popup.centered ? "tour-popup centered" : "tour-popup"}
        style={{ top: popup.top, left: popup.left, width: POPUP_W }}
      >
        <div className="tour-popup-head">
          <span className="tour-progress">
            {index + 1} / {steps.length}
          </span>
          <button type="button" className="icon-button compact" onClick={onClose} title="跳过引导">
            <X size={16} />
          </button>
        </div>
        <h3>{step.title}</h3>
        <p>{step.body}</p>
        <div className="tour-dots">
          {steps.map((_, dotIndex) => (
            <button
              key={dotIndex}
              type="button"
              className={dotIndex === index ? "tour-dot active" : "tour-dot"}
              onClick={() => setIndex(dotIndex)}
              aria-label={`跳到第 ${dotIndex + 1} 步`}
            />
          ))}
        </div>
        <div className="tour-actions">
          <button type="button" className="small-action" onClick={onClose}>
            跳过
          </button>
          <div className="tour-actions-right">
            {!isFirst && (
              <button type="button" className="small-action" onClick={() => setIndex((i) => i - 1)}>
                上一步
              </button>
            )}
            {isLast ? (
              <button type="button" className="primary-action" onClick={onClose}>
                开始使用
              </button>
            ) : (
              <button type="button" className="primary-action" onClick={() => setIndex((i) => i + 1)}>
                下一步
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
