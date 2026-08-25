import { Ban, CheckCircle2, ChevronDown, ChevronRight, Loader2, MessageCircleQuestion, RotateCcw, X } from "lucide-react";
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

/** 默认勾上哪些待选候选。
 *
 * 三类默认不勾（但都仍保留在卡片里，可以手动勾上）：
 * - `hard_blocked`：命中排除词/城市/薪资硬条件。这些候选在列表里是**折叠**的
 *   （见 `blockedIndexes`），默认勾上等于「看不见却会被入库」——实测一次采集的 9 条里
 *   5 条硬阻断全被写进了岗位池。折叠与默认勾选必须用同一个判断，否则就会再次错位。
 * - `existing_job_id`：已在岗位池，避免误触重复合并。
 * - `duplicate_in_thread_id`：与近期候选重复。
 *
 * 初始状态与 commit 之后的重算共用本函数，避免两处条件各写一遍而漂移。
 */
function defaultSelection(items: IngestCandidate[], pendingIndexes: number[]): number[] {
  return pendingIndexes.filter(
    (i) => !items[i]?.hard_blocked && !items[i]?.existing_job_id && items[i]?.duplicate_in_thread_id == null
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
  const [selected, setSelected] = useState<number[]>(defaultSelection(candidates, pendingIndexes));
  const [busy, setBusy] = useState(false);
  const [boardWriteBusyIndex, setBoardWriteBusyIndex] = useState<number | null>(null);
  const [restoreBusyIndex, setRestoreBusyIndex] = useState<number | null>(null);
  const [showBlocked, setShowBlocked] = useState(false);
  const [excludeBusyIndex, setExcludeBusyIndex] = useState<number | null>(null);

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
      setSelected(defaultSelection(next, nextPending));
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

  async function excludeKeyword(index: number, keyword: string) {
    const word = keyword.trim();
    if (!word) return;
    setExcludeBusyIndex(index);
    try {
      await api<{ dealbreakers: string[] }>("/api/profile/dealbreakers", {
        method: "POST",
        ...jsonBody({ word }),
      });
      onError(`已添加排除词「${word}」，后续采集将自动阻断`);
    } catch (err) {
      onError(errorMessage(err, "添加排除词失败"));
    } finally {
      setExcludeBusyIndex(null);
    }
  }

  const blockedIndexes = candidates.map((c, i) => (c.hard_blocked ? i : -1)).filter((i) => i >= 0);
  const visibleIndexes = candidates.map((_, i) => i).filter((i) => !candidates[i].hard_blocked);

  function renderCandidate(item: IngestCandidate, index: number) {
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
              {item.score != null && (
                // 采集初筛候选带匹配分（与岗位池 FitScore 同一套 scoring.score_job）；
                // 一次采回十几条时，没有分数只能一行行读标题。ingest 候选没有这个字段。
                <span className="candidate-score-badge" title="匹配分（与岗位池同一套评分）">
                  {item.score} 分
                </span>
              )}
              {item.hard_blocked && (
                <span className="candidate-blocked-badge" title="命中排除词/城市/薪资硬条件">
                  已排除
                </span>
              )}
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
            {item.reach && (
              <span className="candidate-reach-line">
                {item.reach.level_label ?? item.reach.level} · {item.reach.family_label} · {item.reach.recommendation}
              </span>
            )}
          </span>
        </label>
        {item.reach && (
          <details className="candidate-pack">
            <summary>匹配解释{item.application_pack ? "与投递材料" : ""}</summary>
            {!!item.reach.overlap?.length && <p>能力重合：{item.reach.overlap.join("；")}</p>}
            {!!item.reach.missing_hard?.length && <p>必要条件待核对：{item.reach.missing_hard.join("；")}</p>}
            {!!item.reach.short_term_gaps?.length && <p>可补齐差距：{item.reach.short_term_gaps.join("；")}</p>}
            {item.application_pack && (
              <>
                <p><strong>投递理由：</strong>{item.application_pack.application_reason}</p>
                <pre>{item.application_pack.risk_questions}</pre>
                <details>
                  <summary>查看定制简历版本</summary>
                  <pre>{item.application_pack.resume_version}</pre>
                </details>
              </>
            )}
          </details>
        )}
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
        <div className="candidate-exclude">
          <button
            type="button"
            className="small-action"
            title="把这个标题加入排除词（后续采集自动阻断）"
            disabled={excludeBusyIndex === index}
            onClick={() => void excludeKeyword(index, item.title || "")}
          >
            {excludeBusyIndex === index ? <Loader2 className="spin" size={14} /> : <Ban size={14} />}
            排除
          </button>
        </div>
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
  }

  return (
    <div className="candidate-card" aria-label="入库候选">
      <div className="candidate-head">
        <strong>候选岗位</strong>
        <small>默认不入库；勾选后点「入库选中」</small>
      </div>
      <ul className="candidate-list">
        {visibleIndexes.map((index) => renderCandidate(candidates[index], index))}
      </ul>
      {blockedIndexes.length > 0 && (
        <div className="candidate-blocked-section">
          <button
            type="button"
            className="candidate-blocked-toggle"
            onClick={() => setShowBlocked((v) => !v)}
          >
            {showBlocked ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            已排除岗位（{blockedIndexes.length}）
          </button>
          {showBlocked && (
            <ul className="candidate-list candidate-list-blocked">
              {blockedIndexes.map((index) => renderCandidate(candidates[index], index))}
            </ul>
          )}
        </div>
      )}
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
