import type { TourStep } from "../Tour";
import type { RunStatus } from "../types";

// 聚光灯引导步骤：只指向首屏稳定存在的元素，避免切视图编排，保持简单稳健。
export const TOUR_STEPS: TourStep[] = [
  {
    title: "欢迎使用 job-one-stop",
    body: "推荐先用“手动 + 核心”跑通一轮：扫描 → 聊天确认 → 入库 → 调研和准备。确认结果可信后，再扩大求职面或开启自动驾驶。数据只存本机，不自动投递、不自动发消息。"
  },
  { target: "nav", title: "主导航", body: "聊天是默认入口，拿不准的事先丢进去；确定要推进了，再到岗位池、公司调研、面试准备和待办完成闭环。" },
  {
    target: "automation",
    title: "自动驾驶与求职面",
    body: "关/开控制每日自动扫描；核心、相邻、探索只改变搜索面与岗位族评分，不会放松城市、薪资和排除项。首次使用保持“关 + 核心”，点击“立即扫描”验收一轮。"
  },
  {
    target: "automation",
    title: "看懂扫描结果",
    body: "“发现”是本次采到的原始数量；“硬拦截”是命中城市、薪资或排除项的数量；“待确认”是聊天中等你判断的候选。点击“停止自动化”会阻止后续定时扫描。"
  },
  { target: "metrics", title: "概览与漏斗", body: "顶部一条统计带:岗位总数、高潜、待调研、最高分、草稿,以及已投/面试/Offer/待跟进漏斗。点数字会跳到对应视图。" },
  { target: "collect", title: "其它采集入口", body: "工具栏仍可手动运行 BOSS、beBee、CSV/XLSX 和公众号导入。所有新岗位都先进入聊天候选；返回 0 岗位时先看通知里的过滤、跳过或登录态原因。" },
  { target: "wechat", title: "公众号 / 元宝导入", body: "粘贴元宝回答或 mp.weixin 链接，系统会抓正文并拆出多个岗位；被风控的文章可改为手动粘正文。" },
  { target: "sprint", title: "今日求职冲刺包", body: "一键补评分、挑 Top 岗位、生成面试准备并建待办，最后给出可复制的 Markdown 清单。" },
  { target: "manual", title: "新增岗位", body: "没有链接时也能手动单条录入岗位。所有来源都会汇入同一条管线并跨来源去重。" },
  { target: "guide", title: "随时回看", body: "侧栏的“使用指南”一直可见：可以重新检查配置、查看推荐第一轮流程，或再次启动这套界面导览。" }
];

export const statuses = ["all", "new", "researching", "fit", "applied", "interview", "offer", "rejected", "archived"];

export const jobStatuses = statuses.filter((item) => item !== "all");

export const statusLabels: Record<string, string> = {
  all: "全部",
  new: "新增",
  researching: "待调研",
  fit: "合适",
  applied: "已投递",
  interview: "面试",
  offer: "Offer",
  rejected: "拒绝",
  archived: "归档"
};

export const draftKindLabels: Record<string, string> = {
  boss_message: "沟通草稿",
  communication_draft: "沟通草稿",
  core_pitch: "核心优势话术",
  tailored_resume: "对应简历"
};

export const applicationEventLabels: Record<string, string> = {
  applied: "已投递",
  reply: "已回复",
  interview_invite: "约面",
  rejected: "拒绝",
  offer: "Offer",
  withdrawn: "撤回"
};

export const YUANBAO_PROMPT =
  "帮我找最近【请填写目标城市和目标岗位】相关的招聘公众号文章。\n" +
  "只输出一个 JSON 数组，每项含 title 和 link 两个字段；\n" +
  "link 必须是以 https://mp.weixin.qq.com/s 开头的公众号文章原文链接，不要任何解释或多余文字。";

// 面试后机会评分：6 维人工自评，权重合计 100。前端求和并给出结论分桶（与面试前 JD 评分 FitScore 解耦）。
export const OPPORTUNITY_DIMENSIONS = [
  { key: "现金流", weight: 25 },
  { key: "岗位匹配", weight: 20 },
  { key: "业务闭环", weight: 20 },
  { key: "团队资源", weight: 15 },
  { key: "作息风险", weight: 10 },
  { key: "成长价值", weight: 10 }
] as const;

export const INTERVIEW_ROUNDS = ["一面", "二面", "三面", "HR面", "复试", "终面"];

// 面试后跟进话术的三种静态模板（来自方法论），一键填入复盘的 follow_up 后再按公司改。
export const FOLLOWUP_TEMPLATES = [
  {
    label: "继续推进",
    text:
      "您好，感谢今天的沟通。通过这次交流，我对贵司岗位中独立站 SEO、网站维护和海外推广的工作内容更清楚了。\n\n" +
      "我这边比较匹配的是英文 B2B 内容、关键词规划、GSC/GA4 数据分析、TDK 和基础站内优化。如果后续有进一步面试或测试，我可以围绕贵司官网/产品方向准备更具体的优化思路。"
  },
  {
    label: "再确认重点",
    text:
      "您好，感谢今天沟通。我想再确认一下岗位重点：后续工作更偏官网/独立站 SEO 和 Google 推广，还是平台运营和日常维护为主？" +
      "另外团队里内容、设计、开发和外贸业务是否会有配合？"
  },
  {
    label: "婉拒",
    text:
      "您好，感谢今天沟通。结合岗位实际内容和我目前的求职方向，我这边更希望聚焦英文独立站 SEO、B2B 网站运营和 Google 数据分析方向。" +
      "这个岗位目前可能匹配度不是最高，先不继续推进了，也祝贵司招聘顺利。"
  }
] as const;

export const PAGE_SIZE = 10;
export const JOB_PAGE_SIZE = 20;
export const USAGE_GUIDE_SEEN_KEY = "job-one-stop.usage-guide-seen.v2";
export const ACTIVE_CHAT_THREAD_KEY = "job-one-stop.active-chat-thread.v1";
export const CHAT_USE_AI_KEY = "job-one-stop.chat-use-ai.v1";
export const SIDEBAR_COLLAPSED_KEY = "job-one-stop.sidebar-collapsed.v1";
// 「本版新增」弹窗：存的是"已看过说明的版本号"，不是布尔——只有版本号变了才再弹一次。
// 全新安装（读不到这个键）时不弹，让位给「开始使用」引导，避免首启两个弹窗叠一起。
export const WHATS_NEW_SEEN_KEY = "job-one-stop.whats-new-seen.v1";
// AI「测试连接」的费用确认：勾了"下次不再提示"就存 true，直接发测试请求。
export const AI_TEST_CONFIRM_SKIP_KEY = "job-one-stop.ai-test-confirm-skip.v1";
// 「重置本地缓存」清的就是这一组：全是界面偏好/游标，**不含任何业务数据**。
// 新增 localStorage 键时记得加进来，否则「重置」会漏掉它、用户以为清了其实没清。
export const LOCAL_CACHE_KEYS = [
  USAGE_GUIDE_SEEN_KEY,
  ACTIVE_CHAT_THREAD_KEY,
  CHAT_USE_AI_KEY,
  SIDEBAR_COLLAPSED_KEY,
  WHATS_NEW_SEEN_KEY,
  AI_TEST_CONFIRM_SKIP_KEY
] as const;

export const GLOBAL_BUSY_KEYS = ["source-boss", "source-bebee", "upload", "wechat", "sprint", "manual", "profile", "export"] as const;

export const PREVIEW_SECTION_LABELS: Record<string, string> = {
  decision_rules: "决策规则",
  profile: "个人画像",
  board: "求职看板",
};

// 三态来源标记：AI 成功融合、AI 调用失败已回退、未启用 AI。
// 关键点是把「启用了但调用失败」和「从没配 AI」区分开，避免坏 key 时静默显示「仅规则」。
export const RUN_STATUS_BADGE: Record<RunStatus, { label: string; tone: string; title: string }> = {
  completed: { label: "规则 + AI", tone: "ok", title: "规则先行，AI 已结合上下文补充。" },
  fallback: { label: "规则(AI 调用失败)", tone: "warn", title: "AI 已启用但本次调用失败，已回退到规则结果。可在侧栏「测试连接」查看原因。" },
  rules_only: { label: "仅规则", tone: "muted", title: "未启用 AI，仅使用本地规则。" },
};

// 与 backend/app/services/scoring.py 的 DEFAULT_WEIGHTS 保持一致，仅用于画像还没存过某个维度时
// 的界面兜底显示（新增维度、或很旧的画像行缺键），不参与实际评分——评分永远读 UserProfile.weights。
export const DEFAULT_SCORING_WEIGHTS: Record<string, number> = {
  role_match: 25,
  salary_city: 15,
  growth: 15,
  stability: 15,
  reputation: 10,
  commute_rest: 10,
  interview_roi: 10
};

export const EMPTY_INTERVIEW_FORM = {
  round: "一面",
  interview_date: "",
  interviewer: "",
  real_picture: "",
  score_details: {} as Record<string, number>,
  qa_review: "",
  weaknesses: "",
  next_actions: "",
  follow_up: ""
};
