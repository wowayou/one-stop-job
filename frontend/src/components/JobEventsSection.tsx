import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { hasBusy, type BusyState } from "../hooks/useBusyState";
import { applicationEventLabels } from "../lib/constants";
import type { ApplicationEvent } from "../types";

export function JobEventsSection({
  events,
  busy,
  onAddEvent,
  onDeleteEvent
}: {
  events: ApplicationEvent[];
  busy: BusyState;
  onAddEvent: (payload: { event_type: string; event_date: string; channel?: string; note?: string }) => Promise<void>;
  onDeleteEvent: (event: ApplicationEvent) => Promise<void>;
}) {
  const [form, setForm] = useState({
    event_type: "applied",
    event_date: new Date().toISOString().slice(0, 10),
    channel: "",
    note: ""
  });
  return (
    <>
      <div className="event-list">
        {events.map((item) => (
          <article key={item.id} className="event-item">
            <div className="event-meta">
              <div>
                <strong>{applicationEventLabels[item.event_type] ?? item.event_type}</strong>
                <span>
                  {item.event_date}
                  {item.channel ? ` · ${item.channel}` : ""}
                </span>
              </div>
              <button
                type="button"
                className="icon-button compact"
                title="删除事件"
                onClick={() => onDeleteEvent(item)}
                disabled={hasBusy(busy, `job-event-${item.id}`)}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {item.note && <p className="event-note">{item.note}</p>}
          </article>
        ))}
        {!events.length && <p className="muted">还没有投递事件。记录投递、回复、约面、拒绝和 Offer 后，漏斗分析会更可信。</p>}
      </div>
      <form
        className="research-form event-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!form.event_date) return;
          onAddEvent(form).then(() =>
            setForm((current) => ({
              ...current,
              note: "",
              channel: ""
            }))
          );
        }}
      >
        <div className="inline-fields">
          <label>
            事件
            <select value={form.event_type} onChange={(event) => setForm({ ...form, event_type: event.target.value })}>
              {Object.entries(applicationEventLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            日期
            <input type="date" value={form.event_date} onChange={(event) => setForm({ ...form, event_date: event.target.value })} required />
          </label>
        </div>
        <label>
          渠道
          <input value={form.channel} onChange={(event) => setForm({ ...form, channel: event.target.value })} placeholder="BOSS / 邮件 / 微信 / 内推" />
        </label>
        <label>
          备注
          <textarea value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} placeholder="例如：已附带作品集、对方说下周约面" />
        </label>
        <button className="primary-action" disabled={hasBusy(busy, "job-event")}>
          <Plus size={16} />
          记录事件
        </button>
      </form>
    </>
  );
}
