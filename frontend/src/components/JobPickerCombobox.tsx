import { Search } from "lucide-react";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
import type { Job } from "../types";

// 岗位专属聊天检索框：岗位库常年 99+ 条，原生 <select> 逐条找太慢，换成「输入过滤 + 下拉选」，
// 行为对齐原 select（选中即回填 jobId），不引组件库。列表最多渲染前 50 条，键盘上下+Enter 可选。
export function JobPickerCombobox({
  jobs,
  value,
  onChange,
  disabled,
}: {
  jobs: Job[];
  value: string;
  onChange: (jobId: string) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  // 只在「没有正在编辑」时把 query 同步成当前选中项的文案；编辑中（open=true）不能被这个
  // effect 打断，否则 onChange 里为了让「创建/打开」及时失效而清空 value，会在下一渲染把
  // 刚打的字又冲掉。
  useEffect(() => {
    if (open) return;
    if (!value) {
      setQuery("");
      return;
    }
    const job = jobs.find((item) => String(item.id) === value);
    setQuery(job ? `${job.company_name} · ${job.title}` : "");
  }, [value, jobs, open]);

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  const normalizedQuery = query.trim().toLowerCase();
  const filtered = normalizedQuery
    ? jobs.filter((job) => `${job.title} ${job.company_name}`.toLowerCase().includes(normalizedQuery))
    : jobs;
  const visible = filtered.slice(0, 50);

  function choose(job: Job) {
    onChange(String(job.id));
    setQuery(`${job.company_name} · ${job.title}`);
    setOpen(false);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        setOpen(false);
      }
      return;
    }
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        setOpen(true);
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((i) => Math.min(i + 1, visible.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter" && visible[highlighted]) {
      event.preventDefault();
      choose(visible[highlighted]);
    }
  }

  return (
    <div className="job-picker" ref={containerRef}>
      <div className="job-picker-field">
        <Search size={14} />
        <input
          value={query}
          disabled={disabled}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setHighlighted(0);
            if (value) onChange("");
          }}
          onKeyDown={handleKeyDown}
          placeholder="搜索岗位/公司…"
          aria-label="搜索岗位创建专属聊天"
        />
      </div>
      {open && (
        <div className="job-picker-panel" role="listbox">
          {visible.map((job, index) => (
            <button
              type="button"
              key={job.id}
              role="option"
              aria-selected={String(job.id) === value}
              className={`job-picker-option${index === highlighted ? " active" : ""}`}
              onMouseDown={(event) => {
                // 用 mousedown + preventDefault 抢在 input 的 blur 前完成选中，
                // 避免「点选项」被「先失焦收起面板」抢先导致点不中。
                event.preventDefault();
                choose(job);
              }}
            >
              <strong>{job.company_name}</strong>
              <span>{job.title}</span>
            </button>
          ))}
          {!visible.length && <p className="job-picker-empty">没有匹配的岗位</p>}
          <p className="job-picker-hint">共 {filtered.length} 条，输入以过滤</p>
        </div>
      )}
    </div>
  );
}
