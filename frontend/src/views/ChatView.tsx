import {
  AlertCircle,
  CheckCircle2,
  ImagePlus,
  Info,
  ListChecks,
  Loader2,
  MessageSquareText,
  Pencil,
  Plus,
  Send,
  Trash2,
  X
} from "lucide-react";
import { ClipboardEvent, FormEvent, useEffect, useRef, useState } from "react";
import { api, apiUrl, errorMessage, jsonBody } from "../api";
import { CandidateListCard } from "../components/CandidateListCard";
import { ChatProgress } from "../components/ChatProgress";
import { DecisionAnalysisCard } from "../components/DecisionAnalysisCard";
import { JobPickerCombobox } from "../components/JobPickerCombobox";
import { ACTIVE_CHAT_THREAD_KEY, CHAT_USE_AI_KEY, PREVIEW_SECTION_LABELS } from "../lib/constants";
import type {
  ChatContextPreview,
  ChatMessage,
  ChatReply,
  ChatThread,
  ChatThreadBatchDeleteReply,
  ChatThreadDetail,
  Job
} from "../types";

export function ChatView({
  jobs,
  onOpenJob,
  aiAvailable,
  boardWriteEnabled,
}: {
  jobs: Job[];
  onOpenJob: (job: Job) => void | Promise<void>;
  aiAvailable: boolean;
  boardWriteEnabled: boolean;
}) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<number | null>(() => {
    try {
      const stored = window.localStorage.getItem(ACTIVE_CHAT_THREAD_KEY);
      return stored ? Number(stored) || null : null;
    } catch {
      return null;
    }
  });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  // 「问这个岗位」：入库候选线索里有多个候选时，指名这一条问的是第几个（0 基）。
  // 候选没入库前没有 Job 记录，线程本身挂不住岗位，只能靠这个索引锚定；发送后即清空。
  const [askCandidate, setAskCandidate] = useState<{ index: number; label: string } | null>(null);
  const [imageDataUrl, setImageDataUrl] = useState("");
  const [imageName, setImageName] = useState("");
  const [jobId, setJobId] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState<{ content: string; imageDataUrl: string; imageName: string } | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [deletingThreadId, setDeletingThreadId] = useState<number | null>(null);
  const [manageMode, setManageMode] = useState(false);
  const [selectedThreadIds, setSelectedThreadIds] = useState<Set<number>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ChatContextPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // 「本条不用 AI」：默认开(=可用 AI 时用 AI)，状态存本机，跨会话记住上次选择。
  const [useAiForMessage, setUseAiForMessage] = useState<boolean>(() => {
    try {
      const stored = window.localStorage.getItem(CHAT_USE_AI_KEY);
      return stored === null ? true : stored === "true";
    } catch {
      return true;
    }
  });
  // 阶段进度：结构化决策卡无法逐字流式，改为显式展示「检查规则 → 询问模型 → 整理结果」，让等待可见。
  const [stage, setStage] = useState(0);
  const stageTimers = useRef<number[]>([]);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const selectAllThreadsRef = useRef<HTMLInputElement>(null);

  const activeThread = threads.find((thread) => thread.id === activeThreadId) ?? null;
  const activeJob = activeThread?.job_id ? jobs.find((job) => job.id === activeThread.job_id) ?? null : null;
  // 这条消息实际会不会用到 AI：全局可用 且 本条开关没关。
  const effectiveAiForMessage = aiAvailable && useAiForMessage;

  useEffect(() => {
    try {
      window.localStorage.setItem(CHAT_USE_AI_KEY, String(useAiForMessage));
    } catch {
      // localStorage 只是记住上次选择的锦上添花，写入失败不影响本次发送。
    }
  }, [useAiForMessage]);

  async function loadThreads(preferredId?: number | null) {
    const loaded = await api<ChatThread[]>("/api/chat/threads");
    setThreads(loaded);
    const candidate = preferredId ?? activeThreadId;
    const nextId = candidate && loaded.some((thread) => thread.id === candidate) ? candidate : loaded[0]?.id ?? null;
    setActiveThreadId(nextId);
    return nextId;
  }

  async function loadMessages(threadId: number) {
    const detail = await api<ChatThreadDetail>(`/api/chat/threads/${threadId}`);
    setMessages(detail.messages);
    setThreads((items) => items.map((item) => (item.id === detail.thread.id ? detail.thread : item)));
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadThreads()
      .then((threadId) => (threadId && !cancelled ? loadMessages(threadId) : undefined))
      .catch((err) => !cancelled && setError(errorMessage(err, "聊天记录加载失败")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeThreadId || loading) return;
    try {
      window.localStorage.setItem(ACTIVE_CHAT_THREAD_KEY, String(activeThreadId));
    } catch {
      // Local storage is optional; SQLite remains the source of truth.
    }
    setError("");
    setPreview(null);
    loadMessages(activeThreadId).catch((err) => setError(errorMessage(err, "聊天记录加载失败")));
  }, [activeThreadId]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pendingMessage, sending]);

  useEffect(() => {
    setRenaming(false);
    setRenameDraft(activeThread?.title ?? "");
  }, [activeThreadId, activeThread?.title]);

  async function createThread(kind: "general" | "job") {
    setError("");
    if (kind === "job" && !jobId) {
      setError("请先选择一个岗位。");
      return;
    }
    try {
      const thread = await api<ChatThread>("/api/chat/threads", {
        method: "POST",
        ...jsonBody({ kind, job_id: kind === "job" ? Number(jobId) : null })
      });
      await loadThreads(thread.id);
      setActiveThreadId(thread.id);
      await loadMessages(thread.id);
      if (kind === "job") setJobId("");
    } catch (err) {
      setError(errorMessage(err, "创建聊天失败"));
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim() || (imageDataUrl ? "请分析这张截图。" : "");
    if (!activeThreadId || !content || sending) return;
    const sentImageDataUrl = imageDataUrl;
    const sentImageName = imageName;
    const sentCandidateIndex = askCandidate?.index ?? null;
    setSending(true);
    setError("");
    setDraft("");
    setImageDataUrl("");
    setImageName("");
    setAskCandidate(null);
    setPendingMessage({ content, imageDataUrl: sentImageDataUrl, imageName: sentImageName });
    // 阶段推进：规则先跑（立即），实际会用 AI 时约 0.4s 进入「询问模型」，兜底再显示「整理结果」。
    // 只是等待时的可见进度，不改变后端流程；请求返回时立刻清掉。本条关了 AI 时按「仅规则」的
    // 两步走，不显示不会发生的「询问模型」。
    setStage(0);
    stageTimers.current.forEach((id) => window.clearTimeout(id));
    stageTimers.current = effectiveAiForMessage
      ? [window.setTimeout(() => setStage(1), 400), window.setTimeout(() => setStage(2), 3500)]
      : [window.setTimeout(() => setStage(2), 250)];
    try {
      const reply = await api<ChatReply>(`/api/chat/threads/${activeThreadId}/messages`, {
        method: "POST",
        ...jsonBody({
          content,
          image_data_url: sentImageDataUrl || null,
          image_name: sentImageName || null,
          use_ai: useAiForMessage,
          candidate_index: sentCandidateIndex
        })
      });
      setMessages((items) => [...items, reply.user_message, reply.assistant_message]);
      await loadThreads(activeThreadId);
      setPreview(null);
    } catch (err) {
      setDraft(content);
      setImageDataUrl(sentImageDataUrl);
      setImageName(sentImageName);
      setAskCandidate(askCandidate);
      setError(errorMessage(err, "分析失败；消息可能已保存在本机，可刷新查看"));
    } finally {
      stageTimers.current.forEach((id) => window.clearTimeout(id));
      stageTimers.current = [];
      setStage(0);
      setPendingMessage(null);
      setSending(false);
    }
  }

  // 发送前预览：显示启用 AI 时这一线程会离开本机的固定上下文（决策规则/画像/看板 + 岗位事实 + 最近对话）。
  async function togglePreview() {
    if (preview) {
      setPreview(null);
      return;
    }
    if (!activeThreadId || previewLoading) return;
    setPreviewLoading(true);
    setError("");
    try {
      const result = await api<ChatContextPreview>(`/api/chat/threads/${activeThreadId}/context-preview`);
      setPreview(result);
    } catch (err) {
      setError(errorMessage(err, "预览加载失败"));
    } finally {
      setPreviewLoading(false);
    }
  }

  async function renameThread(event: FormEvent) {
    event.preventDefault();
    const title = renameDraft.trim();
    if (!activeThreadId || !title || title === activeThread?.title) {
      setRenaming(false);
      setRenameDraft(activeThread?.title ?? "");
      return;
    }
    try {
      const updated = await api<ChatThread>(`/api/chat/threads/${activeThreadId}`, {
        method: "PATCH",
        ...jsonBody({ title })
      });
      setThreads((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      setRenaming(false);
      setError("");
    } catch (err) {
      setError(errorMessage(err, "重命名失败"));
    }
  }

  async function deleteThread(thread: ChatThread) {
    if (!window.confirm(`删除聊天「${thread.title}」？会同时删除消息与截图附件，不可恢复。`)) return;
    setDeletingThreadId(thread.id);
    setError("");
    try {
      await api<{ deleted: boolean; id: number }>(`/api/chat/threads/${thread.id}`, { method: "DELETE" });
      setThreads((items) => items.filter((item) => item.id !== thread.id));
      if (activeThreadId === thread.id) {
        setActiveThreadId(null);
        setMessages([]);
      }
    } catch (err) {
      setError(errorMessage(err, "删除聊天失败"));
    } finally {
      setDeletingThreadId(null);
    }
  }

  function toggleManageMode() {
    setManageMode((value) => !value);
    setSelectedThreadIds(new Set());
  }

  function toggleThreadSelected(threadId: number, checked: boolean) {
    setSelectedThreadIds((current) => {
      const next = new Set(current);
      if (checked) next.add(threadId);
      else next.delete(threadId);
      return next;
    });
  }

  // 全选控件的三态：全选 / 半选(indeterminate) / 未选，随 selectedThreadIds 与 threads 派生，
  // 和逐行 checkbox 天然双向同步——任何一边变化都会触发重渲染重新算出这三个值。
  const allThreadsSelected = threads.length > 0 && threads.every((thread) => selectedThreadIds.has(thread.id));
  const someThreadsSelected = selectedThreadIds.size > 0 && !allThreadsSelected;

  useEffect(() => {
    if (selectAllThreadsRef.current) selectAllThreadsRef.current.indeterminate = someThreadsSelected;
  }, [someThreadsSelected]);

  function toggleAllThreads(checked: boolean) {
    setSelectedThreadIds(checked ? new Set(threads.map((thread) => thread.id)) : new Set());
  }

  async function batchDeleteThreads() {
    const ids = Array.from(selectedThreadIds);
    if (!ids.length || batchDeleting) return;
    if (!window.confirm(`将永久删除 ${ids.length} 个对话及其消息、截图附件，不可恢复。`)) return;
    setBatchDeleting(true);
    setError("");
    try {
      await api<ChatThreadBatchDeleteReply>("/api/chat/threads/batch-delete", {
        method: "POST",
        ...jsonBody({ ids }),
      });
      const deletedIds = new Set(ids);
      setThreads((items) => items.filter((item) => !deletedIds.has(item.id)));
      if (activeThreadId != null && deletedIds.has(activeThreadId)) {
        setActiveThreadId(null);
        setMessages([]);
      }
      setManageMode(false);
      setSelectedThreadIds(new Set());
    } catch (err) {
      setError(errorMessage(err, "批量删除失败"));
    } finally {
      setBatchDeleting(false);
    }
  }

  function chooseImage(file?: File) {
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setError("截图只支持 PNG、JPEG 或 WebP。")
      return;
    }
    if (file.size > 4 * 1024 * 1024) {
      setError("截图不能超过 4 MB。")
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setImageDataUrl(typeof reader.result === "string" ? reader.result : "");
      setImageName(file.name);
      setError("");
    };
    reader.onerror = () => setError("截图读取失败，请重试。");
    reader.readAsDataURL(file);
  }

  function pasteImage(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageItem = Array.from(event.clipboardData.items).find(
      (item) => item.kind === "file" && item.type.startsWith("image/")
    );
    const file = imageItem?.getAsFile();
    if (!file) return;
    const namedFile = file.name
      ? file
      : new File([file], `剪贴板截图-${new Date().toISOString().replace(/:/g, "-")}.${file.type.split("/")[1] || "png"}`, { type: file.type });
    chooseImage(namedFile);
    if (!event.clipboardData.getData("text/plain")) event.preventDefault();
  }

  return (
    <section className="chat-shell" aria-label="决策聊天">
      <aside className="chat-sidebar">
        <div className="chat-sidebar-head">
          <div><strong>对话</strong><small>刷新页面后仍会保留</small></div>
          <div className="row-actions">
            <button
              type="button"
              className={manageMode ? "icon-button compact marked" : "icon-button compact"}
              title={manageMode ? "退出管理" : "管理对话"}
              aria-label={manageMode ? "退出管理" : "管理对话"}
              onClick={toggleManageMode}
              disabled={sending}
            >
              <ListChecks size={15} />
            </button>
            <button className="small-action" type="button" onClick={() => createThread("general")} disabled={sending}><Plus size={15} /> 新对话</button>
          </div>
        </div>
        <div className="chat-job-create">
          <JobPickerCombobox jobs={jobs} value={jobId} onChange={setJobId} disabled={sending} />
          <button className="small-action" type="button" onClick={() => createThread("job")} disabled={!jobId || sending}>创建 / 打开</button>
        </div>
        <div className="chat-thread-list">
          {threads.map((thread) => (
            <div className="chat-thread-row" key={thread.id}>
              <button type="button" className={thread.id === activeThreadId ? "chat-thread active" : "chat-thread"} onClick={() => setActiveThreadId(thread.id)} disabled={sending}>
                <span>{thread.kind === "job" ? "岗位" : thread.kind === "ingest" ? "入库" : "通用"}</span>
                <strong>{thread.title}</strong>
                <small>{thread.last_message || "还没有消息"}</small>
              </button>
              {manageMode ? (
                <span className="chat-thread-select">
                  <input
                    type="checkbox"
                    checked={selectedThreadIds.has(thread.id)}
                    onChange={(event) => toggleThreadSelected(thread.id, event.target.checked)}
                    aria-label={`选择聊天 ${thread.title}`}
                  />
                </span>
              ) : (
                <button
                  type="button"
                  className="icon-button compact chat-thread-delete"
                  title="删除聊天"
                  aria-label={`删除聊天 ${thread.title}`}
                  onClick={() => deleteThread(thread)}
                  disabled={sending || deletingThreadId === thread.id}
                >
                  {deletingThreadId === thread.id ? <Loader2 className="spin" size={13} /> : <Trash2 size={13} />}
                </button>
              )}
            </div>
          ))}
          {!loading && !threads.length && <p className="muted chat-empty-copy">先创建一个通用聊天，或者给某个岗位开专属聊天。</p>}
        </div>
        {manageMode && (
          <div className="chat-thread-batch-bar">
            <div className="chat-thread-batch-top">
              <label className="chat-select-all">
                <input
                  ref={selectAllThreadsRef}
                  type="checkbox"
                  checked={allThreadsSelected}
                  onChange={(event) => toggleAllThreads(event.target.checked)}
                  disabled={!threads.length}
                  aria-label="全选对话"
                />
                全选
              </label>
              <span>已选 {selectedThreadIds.size} 个</span>
            </div>
            <button className="primary-action" type="button" disabled={!selectedThreadIds.size || batchDeleting} onClick={() => void batchDeleteThreads()}>
              {batchDeleting ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
              删除选中（{selectedThreadIds.size}）
            </button>
          </div>
        )}
      </aside>

      <div className="chat-main">
        {activeThread ? (
          <>
            <header className="chat-head">
              <div className="chat-head-info">
                <div className="chat-title-line">
                  <span className="chat-kind">{activeThread.kind === "job" ? "岗位聊天" : activeThread.kind === "ingest" ? "入库候选" : "通用聊天"}</span>
                  {renaming ? (
                    <form className="chat-rename" onSubmit={renameThread}>
                      <input autoFocus maxLength={120} value={renameDraft} onChange={(event) => setRenameDraft(event.target.value)} aria-label="聊天名称" />
                      <button className="small-action" type="submit" disabled={!renameDraft.trim()}>保存</button>
                      <button className="icon-button compact" type="button" title="取消重命名" onClick={() => { setRenaming(false); setRenameDraft(activeThread.title); }}><X size={14} /></button>
                    </form>
                  ) : (
                    <>
                      <h2>{activeThread.title}</h2>
                      <button className="icon-button compact chat-rename-trigger" type="button" title="重命名聊天" aria-label="重命名聊天" onClick={() => setRenaming(true)} disabled={sending}><Pencil size={14} /></button>
                    </>
                  )}
                </div>
                <p>规则优先，AI 可选；内容留在本机，启用 AI 时才发送本次材料与所需上下文。</p>
              </div>
              {activeJob && <button className="small-action" type="button" onClick={() => onOpenJob(activeJob)}>查看岗位</button>}
            </header>

            <div className="chat-messages" aria-live="polite">
              {!messages.length && !loading && (
                <div className="chat-welcome">
                  <MessageSquareText size={28} />
                  <h3>把你拿不准的事情直接丢进来</h3>
                  <p>可以粘贴 JD、招聘方回复、网页链接旁的正文，或描述你现在的约束。信息不足时，助手会明确告诉你还缺什么。</p>
                  <div className="chat-prompts">
                    {["这个岗位值不值得聊？我最应该先确认什么？", "招聘方这样回复，我现在怎么回？", "这件事有价值到需要沉淀吗？"].map((prompt) => (
                      <button type="button" className="small-action" key={prompt} onClick={() => setDraft(prompt)}>{prompt}</button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((message) => {
                const analysis = message.role === "assistant" ? message.metadata_json?.analysis : undefined;
                const candidates = message.role === "assistant" ? message.metadata_json?.candidates : undefined;
                return (
                  <article key={message.id} className={`chat-message ${message.role}`}>
                    <div className="chat-message-label">{message.role === "user" ? "你" : "助手"}</div>
                    <div className="chat-bubble">
                      {/* 回答针对哪个候选：气泡正文显示的是 analysis.summary，不单独渲染这行的话，
                          后端写进 content 的「针对 ① …」在 Web 上就看不见了（手机上能看见）。 */}
                      {message.metadata_json?.anchor?.kind === "candidate" && message.metadata_json.anchor.label && (
                        <p className="chat-answer-anchor">针对 {message.metadata_json.anchor.label}</p>
                      )}
                      <p>{analysis?.summary || message.content}</p>
                      {message.metadata_json?.attachment?.kind === "image" && (
                        <img className="chat-attachment" src={apiUrl(`/api/chat/attachments/${message.metadata_json.attachment.id}`)} alt={message.metadata_json.attachment.name || "聊天截图"} />
                      )}
                      {analysis && <DecisionAnalysisCard analysis={analysis} runStatus={message.metadata_json?.run_status ?? (message.metadata_json?.ai_used ? "completed" : "rules_only")} />}
                      {!!candidates?.length && (
                        <CandidateListCard
                          threadId={activeThreadId!}
                          messageId={message.id}
                          candidates={candidates}
                          boardWriteEnabled={boardWriteEnabled}
                          onAsk={(index, label) => {
                            setAskCandidate({ index, label });
                            composerRef.current?.focus();
                          }}
                          onUpdated={(updated) => {
                            setMessages((items) => items.map((m) => (m.id === updated.id ? updated : m)));
                            void loadThreads(activeThreadId);
                          }}
                          onError={(msg) => setError(msg)}
                        />
                      )}
                    </div>
                  </article>
                );
              })}
              {pendingMessage && (
                <article className="chat-message user pending" aria-label="消息已发送，等待分析">
                  <div className="chat-message-label">你</div>
                  <div className="chat-bubble">
                    <p>{pendingMessage.content}</p>
                    {pendingMessage.imageDataUrl && <img className="chat-attachment" src={pendingMessage.imageDataUrl} alt={pendingMessage.imageName || "待分析截图"} />}
                    <small className="chat-pending-label"><CheckCircle2 size={13} />已发送</small>
                  </div>
                </article>
              )}
              {sending && (
                <article className="chat-message assistant">
                  <div className="chat-message-label">助手</div>
                  <div className="chat-bubble chat-thinking">
                    <ChatProgress stage={stage} aiAvailable={effectiveAiForMessage} />
                  </div>
                </article>
              )}
              <div ref={messageEndRef} />
            </div>

            {preview && (
              <div className="chat-preview" aria-label="发送给 AI 的内容预览">
                <div className="chat-preview-head">
                  <div>
                    <strong>
                      {aiAvailable && !useAiForMessage
                        ? "本条不发送任何内容给 AI"
                        : preview.ai_enabled
                        ? "启用 AI 时会发送以下内容"
                        : "当前未启用 AI，不会发送任何内容"}
                    </strong>
                    <small>
                      {aiAvailable && !useAiForMessage
                        ? "已为这一条关闭「本条用 AI」，只走本地规则；重新勾选后即可恢复。"
                        : preview.ai_enabled
                        ? `模型 ${preview.model} · 固定上下文约 ${preview.context_chars_total} 字 · 最近对话 ${preview.conversation_count} 条`
                        : "配置并测试连接后，这里会显示将要发送的上下文。"}
                    </small>
                  </div>
                  <button type="button" className="icon-button compact" title="关闭预览" onClick={() => setPreview(null)}><X size={14} /></button>
                </div>
                {preview.ai_enabled && useAiForMessage && (
                  <>
                    <div className="chat-preview-sections">
                      {preview.sections.length ? preview.sections.map((section) => (
                        <details key={section.key} className="chat-preview-section">
                          <summary>{PREVIEW_SECTION_LABELS[section.key] ?? section.key}<span>{section.chars} 字</span></summary>
                          <pre>{section.content}</pre>
                        </details>
                      )) : <p className="muted">未配置外部上下文仓库，仅发送本地规则结果与对话。</p>}
                      {!!Object.keys(preview.job_context).length && (
                        <details className="chat-preview-section">
                          <summary>岗位事实<span>{Object.keys(preview.job_context).length} 项</span></summary>
                          <pre>{JSON.stringify(preview.job_context, null, 2)}</pre>
                        </details>
                      )}
                    </div>
                    <p className="chat-preview-note"><Info size={13} />{preview.note}</p>
                  </>
                )}
              </div>
            )}

            <form className="chat-composer" onSubmit={sendMessage}>
              {error && <div className="chat-error"><AlertCircle size={15} />{error}</div>}
              {askCandidate && (
                <div className="chat-ask-anchor">
                  <span>针对 {askCandidate.label}</span>
                  <button type="button" className="icon-button" title="改回不指定候选" onClick={() => setAskCandidate(null)}><X size={14} /></button>
                </div>
              )}
              {imageDataUrl && (
                <div className="chat-attachment-preview">
                  <img src={imageDataUrl} alt={imageName || "待发送截图"} />
                  <span>{imageName}</span>
                  <button type="button" className="icon-button" title="移除截图" onClick={() => { setImageDataUrl(""); setImageName(""); }}><X size={15} /></button>
                </div>
              )}
              <textarea
                ref={composerRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onPaste={pasteImage}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                rows={2}
                maxLength={12000}
                placeholder="粘贴材料或描述问题。Enter 发送，Shift + Enter 换行。"
                disabled={sending}
              />
              <div className="chat-composer-foot">
                <div className="chat-attachment-control">
                  <input ref={imageInputRef} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => { chooseImage(event.target.files?.[0]); event.target.value = ""; }} />
                  <button className="small-action" type="button" onClick={() => imageInputRef.current?.click()} disabled={sending}><ImagePlus size={15} />截图</button>
                  <button className="small-action" type="button" onClick={togglePreview} disabled={sending || previewLoading} title="查看启用 AI 时这一线程会发送给模型的固定上下文">
                    {previewLoading ? <Loader2 className="spin" size={15} /> : <Info size={15} />}{preview ? "收起预览" : "预览发送内容"}
                  </button>
                  {aiAvailable && (
                    <label className="chat-ai-toggle" title="关闭后，这一条只走规则引擎，不会发送给 AI；下一条可以再打开">
                      <input
                        type="checkbox"
                        checked={useAiForMessage}
                        disabled={sending}
                        onChange={(event) => setUseAiForMessage(event.target.checked)}
                      />
                      本条用 AI
                    </label>
                  )}
                  <small>也可按 Ctrl + V 直接粘贴截图</small>
                </div>
                <button className="primary-action" disabled={(!draft.trim() && !imageDataUrl) || sending}>
                  {sending ? <Loader2 className="spin" size={17} /> : <Send size={17} />}{sending ? "分析中" : "发送"}
                </button>
              </div>
            </form>
          </>
        ) : (
          <div className="chat-welcome standalone">
            <MessageSquareText size={30} />
            <h3>先创建一条对话</h3>
            <p>通用聊天适合随手判断；岗位聊天会自动带上岗位库里的事实。</p>
            <button className="primary-action" type="button" onClick={() => createThread("general")}><Plus size={17} />新建通用聊天</button>
            {error && <div className="chat-error"><AlertCircle size={15} />{error}</div>}
          </div>
        )}
      </div>
    </section>
  );
}
