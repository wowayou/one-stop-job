import { CheckCircle2, Loader2, MessageCircleQuestion, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import { api, errorMessage, jsonBody } from "../api";
import { buildInboxLinePreview } from "../lib/format";
import type { BoardWriteResult, CandidateAdvice, ChatMessage, IngestCandidate } from "../types";

// 与后端回执/建议里的序号一致（services/advice.format_advice_block、decision_reply.candidate_label），
// 手机上看到「② 广告优化师」就能直接打 `?2` 指名同一个候选。
const MARKERS = "①②③④⑤⑥⑦⑧⑨⑩".split("");

/** 候选卡里的初步建议：与手机端回执同一份判断（后端 services/advice.py），这里排成三行。
 * 只是「值不值得推进」的只读结论，不影响勾选与入库口径，所以放在勾选框之外、不可点。 */
function CandidateAdviceBlock({ advice }: { advice: CandidateAdvice }) {
  const priorityClass = advice.priority === "待确认" ? "unknown" : advice.priority.toLowerCase();
  const reason = (advice.reasons?.length ? advice.reasons.join("；") : advice.summary) || "";
  return (
    <div className="candidate-advice">
      <div className="candidate-advice-head">
        <span className={`priority priority-${priorityClass}`}>{advice.priority}</span>
        <strong>{advice.direction}</strong>
        <small>→ {advice.next_action}</small>
        {!advice.ai_used && <small className="candidate-advice-mode">仅规则</small>}
      </div>
      {!!advice.hard_conditions?.length && (
        <p className="candidate-advice-hard">硬条件：{advice.hard_conditions.join("；")}</p>
      )}
      {reason && <p>理由：{reason}</p>}
      {advice.ask_first?.length ? (
        <p>先问：{advice.ask_first.join("；")}</p>
      ) : (
        advice.action_text && <p>下一步：{advice.action_text}</p>
      )}
    </div>
  );
}

export function CandidateListCard({
  threadId,
  messageId,
  candidates,
  boardWriteEnabled,
  onAsk,
  onUpdated,
  onError,
}: {
  threadId: number;
  messageId: number;
  candidates: IngestCandidate[];
  boardWriteEnabled: boolean;
  /** 「问这个」：把下一条提问锚定到这个候选（候选没入库前没有 Job，线程挂不住岗位）。 */
  onAsk?: (index: number, label: string) => void;
  onUpdated: (message: ChatMessage) => void;
  onError: (message: string) => void;
}) {
  const pendingIndexes = candidates.map((c, i) => (c.status === "pending" || !c.status ? i : -1)).filter((i) => i >= 0);
  // 已在岗位池 / 与近期候选重复的默认不勾选（避免误触重复合并），但仍保留在待处理列表里，用户可主动勾上。
  const [selected, setSelected] = useState<number[]>(
    pendingIndexes.filter((i) => !candidates[i]?.existing_job_id && candidates[i]?.duplicate_in_thread_id == null)
  );
  const [busy, setBusy] = useState(false);
  const [boardWriteBusyIndex, setBoardWriteBusyIndex] = useState<number | null>(null);
  const [restoreBusyIndex, setRestoreBusyIndex] = useState<number | null>(null);

  function toggle(index: number) {
    if (candidates[index]?.status === "committed" || candidates[index]?.status === "skipped") return;
    setSelected((prev) => (prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index]));
  }

  async function commit(indexes: number[]) {
    setBusy(true);
    try {
      const reply = await api<{ assistant_message: ChatMessage }>(`/api/chat/threads/${threadId}/candidates/commit`, {
        method: "POST",
        ...jsonBody({ message_id: messageId, indexes }),
      });
      onUpdated(reply.assistant_message);
      const next = reply.assistant_message.metadata_json?.candidates ?? [];
      const nextPending = next.map((c, i) => (c.status === "pending" || !c.status ? i : -1)).filter((i) => i >= 0);
      setSelected(nextPending.filter((i) => !next[i]?.existing_job_id && next[i]?.duplicate_in_thread_id == null));
    } catch (err) {
      onError(errorMessage(err, "入库失败"));
    } finally {
      setBusy(false);
    }
  }

  async function writeToBoard(index: number) {
    setBoardWriteBusyIndex(index);
    try {
      const reply = await api<{ assistant_message: ChatMessage; results: BoardWriteResult[] }>(
        `/api/chat/threads/${threadId}/candidates/board-write`,
        { method: "POST", ...jsonBody({ message_id: messageId, indexes: [index] }) }
      );
      onUpdated(reply.assistant_message);
      const result = reply.results.find((item) => item.index === index);
      if (result && !result.ok) {
        onError(result.reason);
      }
    } catch (err) {
      onError(errorMessage(err, "写入看板失败"));
    } finally {
      setBoardWriteBusyIndex(null);
    }
  }

  async function restore(index: number) {
    setRestoreBusyIndex(index);
    try {
      const reply = await api<{ assistant_message: ChatMessage; results: BoardWriteResult[] }>(
        `/api/chat/threads/${threadId}/candidates/restore`,
        { method: "POST", ...jsonBody({ message_id: messageId, indexes: [index] }) }
      );
      onUpdated(reply.assistant_message);
      const result = reply.results.find((item) => item.index === index);
      if (result && !result.ok) {
        onError(result.reason);
        return;
      }
      // 恢复成功后并入待勾选集合，免得用户还要再手动勾一次。
      setSelected((prev) => (prev.includes(index) ? prev : [...prev, index]));
    } catch (err) {
      onError(errorMessage(err, "恢复失败"));
    } finally {
      setRestoreBusyIndex(null);
    }
  }

  return (
    <div className="candidate-card" aria-label="入库候选">
      <div className="candidate-head">
        <strong>候选岗位</strong>
        <small>默认不入库；勾选后点「入库选中」</small>
      </div>
      <ul className="candidate-list">
        {candidates.map((item, index) => {
          const status = item.status || "pending";
          return (
            <li key={`${item.title}-${index}`} className={`candidate-item status-${status}`}>
              <label>
                {status === "pending" ? (
                  <input
                    type="checkbox"
                    checked={selected.includes(index)}
                    disabled={busy}
                    onChange={() => toggle(index)}
                  />
                ) : (
                  // 已入库/已跳过的候选不再是可勾选项，不渲染空 checkbox；用同尺寸状态图标占位对齐。
                  <span className={`candidate-status-icon ${status}`} aria-hidden="true">
                    {status === "committed" ? <CheckCircle2 size={16} /> : <X size={16} />}
                  </span>
                )}
                <span className="candidate-body">
                  <span className="candidate-title-row">
                    <strong>{item.title || "未命名岗位"}</strong>
                    {item.existing_job_id != null && (
                      <span className="candidate-existing-badge" title={`已在岗位池 · #${item.existing_job_id}`}>
                        已在岗位池
                      </span>
                    )}
                    {item.duplicate_in_thread_id != null && (
                      <span className="candidate-duplicate-badge" title={`与近期聊天里的候选重复 · 线程 #${item.duplicate_in_thread_id}`}>
                        重复候选
                      </span>
                    )}
                  </span>
                  <small>
                    {[item.company_name, item.salary_text, [item.city, item.area].filter(Boolean).join(" · "), item.source]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  {status === "committed" && item.job_id != null && <em>已入库 · #{item.job_id}</em>}
                  {status === "skipped" && <em>已跳过</em>}
                </span>
              </label>
              {item.advice && <CandidateAdviceBlock advice={item.advice} />}
              {onAsk && (
                <div className="candidate-ask">
                  <button
                    type="button"
                    className="small-action"
                    title="把下一条提问锁定到这个岗位"
                    onClick={() => onAsk(index, `${MARKERS[index] ?? index + 1} ${item.title || "未命名岗位"}`)}
                  >
                    <MessageCircleQuestion size={14} />
                    问这个
                  </button>
                </div>
              )}
              {status === "committed" && boardWriteEnabled && (
                <div className="candidate-board-write">
                  <code className="candidate-board-line">{buildInboxLinePreview(item)}</code>
                  <button
                    className="small-action"
                    type="button"
                    disabled={!!item.board_written || boardWriteBusyIndex === index}
                    onClick={() => void writeToBoard(index)}
                  >
                    {item.board_written ? (
                      <>
                        <CheckCircle2 size={14} />
                        已写入看板
                      </>
                    ) : boardWriteBusyIndex === index ? (
                      "写入中…"
                    ) : (
                      "写入看板"
                    )}
                  </button>
                </div>
              )}
              {status === "skipped" && (
                <div className="candidate-restore">
                  <button
                    type="button"
                    className="icon-button compact"
                    title="恢复为待选"
                    aria-label={`恢复候选 ${item.title || "未命名岗位"} 为待选`}
                    disabled={restoreBusyIndex === index}
                    onClick={() => void restore(index)}
                  >
                    {restoreBusyIndex === index ? <Loader2 className="spin" size={14} /> : <RotateCcw size={14} />}
                  </button>
                  <span>恢复为待选</span>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {pendingIndexes.length > 0 && (
        <div className="candidate-actions">
          <button className="primary-action" type="button" disabled={busy || !selected.length} onClick={() => void commit(selected)}>
            {busy ? "处理中…" : `入库选中（${selected.length}）`}
          </button>
          <button className="small-action" type="button" disabled={busy} onClick={() => void commit([])}>
            全部跳过
          </button>
        </div>
      )}
    </div>
  );
}
