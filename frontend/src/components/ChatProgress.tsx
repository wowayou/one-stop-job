import { CheckCircle2, Loader2 } from "lucide-react";

// 阶段进度：结构化决策卡无法逐字流式，改为把等待拆成可见的三步。
// 规则检查始终第一步；询问模型仅在启用 AI 时出现；整理结果收尾。stage 由发送时的定时器推进。
export function ChatProgress({ stage, aiAvailable }: { stage: number; aiAvailable: boolean }) {
  const steps = aiAvailable
    ? ["检查规则", "询问模型", "整理建议"]
    : ["检查规则", "整理建议"];
  // 无 AI 时只有两步：stage 0=检查规则，stage 2=整理建议，映射到 steps 下标 0/1。
  const activeIndex = aiAvailable ? stage : stage === 0 ? 0 : 1;
  return (
    <div className="chat-progress" aria-live="polite">
      {steps.map((label, index) => {
        const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "wait";
        return (
          <span key={label} className={`chat-progress-step ${state}`}>
            {state === "done" ? <CheckCircle2 size={14} /> : state === "active" ? <Loader2 className="spin" size={14} /> : <span className="chat-progress-dot" />}
            {label}
          </span>
        );
      })}
    </div>
  );
}
