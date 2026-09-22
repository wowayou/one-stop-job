import { ChevronDown, Loader2 } from "lucide-react";
import { ComponentType, useEffect, useRef, useState } from "react";

export type CollectMenuItem = {
  key: string;
  label: string;
  hint?: string;
  icon: ComponentType<{ size?: number | string }>;
  onClick: () => void;
  busy?: boolean;
};

/**
 * 采集入口下拉：把 BOSS/beBee/海尔/海信/CSV 导入/公众号 等「取数」动作收进一个按钮，
 * 避免 topbar 图标越铺越长（来源会持续增加）。冲刺包、新增岗位不属于取数，保留在外面。
 *
 * 行为对齐 JobPickerCombobox：mousedown 点外部即关、Esc 关；不引组件库。触发键上的
 * 转圈表示「有任一采集/导入在跑」，选项各自也显示自己的忙态。
 */
export function CollectMenu({ items, disabled }: { items: CollectMenuItem[]; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const anyBusy = items.some((item) => item.busy);

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div className="collect-menu" ref={containerRef}>
      <button
        type="button"
        data-tour="collect"
        className="icon-button"
        title="采集 / 导入岗位"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        {anyBusy ? <Loader2 size={18} className="spin" /> : <ChevronDown size={18} />}
      </button>
      {open && (
        <ul className="collect-menu-list" role="menu">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.key} role="none">
                <button
                  type="button"
                  role="menuitem"
                  disabled={disabled || item.busy}
                  onClick={() => {
                    setOpen(false);
                    item.onClick();
                  }}
                >
                  {item.busy ? <Loader2 size={16} className="spin" /> : <Icon size={16} />}
                  <span className="collect-menu-label">{item.label}</span>
                  {item.hint && <small>{item.hint}</small>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
