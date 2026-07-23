export type FitScore = {
  id: number;
  job_id: number;
  total: number;
  hard_blocked: boolean;
  details: {
    hard_reasons?: string[];
    dimensions?: Record<string, { score: number; weight: number; note: string }>;
  };
  created_at: string;
};

export type JobSourceLink = {
  id: number;
  job_id: number;
  source: string;
  external_id: string;
  url?: string | null;
  title?: string | null;
  company_name?: string | null;
  published_at?: string | null;
  first_seen_at: string;
  last_seen_at: string;
};

export type Job = {
  id: number;
  source: string;
  external_id: string;
  url?: string | null;
  title: string;
  company_id?: number | null;
  company_name: string;
  salary_text?: string | null;
  salary_min_k?: number | null;
  salary_max_k?: number | null;
  salary_avg_k?: number | null;
  annual_salary_w?: number | null;
  city?: string | null;
  area?: string | null;
  experience?: string | null;
  degree?: string | null;
  skills?: string | null;
  description?: string | null;
  recruiter?: string | null;
  recruiter_title?: string | null;
  recruiter_is_hr: boolean;
  status: string;
  recruitment_status: string;
  favorite: boolean;
  published_at?: string | null;
  last_seen_at: string;
  canonical_key?: string | null;
  collected_at: string;
  status_changed_at?: string | null;
  latest_score?: FitScore | null;
  source_links?: JobSourceLink[];
};

export type ApplicationEvent = {
  id: number;
  job_id: number;
  event_type: string;
  event_date: string;
  channel?: string | null;
  note: string;
  created_at: string;
  updated_at: string;
};

export type JobBulkUpdateResult = {
  updated: number;
  jobs: Job[];
};

export type Company = {
  id: number;
  name: string;
  website?: string | null;
  industry?: string | null;
  size?: string | null;
  stage?: string | null;
  location?: string | null;
  risk_level: string;
  notes?: string | null;
  jobs_count?: number;
  evidence_count?: number;
};

export type ResearchItem = {
  id: number;
  company_id: number;
  source_type: string;
  source_url?: string | null;
  title: string;
  summary: string;
  sentiment: string;
  confidence: number;
  captured_at: string;
};

export type UserProfile = {
  id: number;
  target_titles: string;
  target_cities: string;
  salary_min_k: number;
  salary_max_k: number;
  skills: string;
  strengths: string;
  work_experience: string;
  dealbreakers: string;
  commute_preferences: string;
  weights: Record<string, number>;
};

export type AiStatus = {
  enabled_in_config: boolean;
  available: boolean;
  provider: string;
  model: string;
  api_key_configured: boolean;
  base_url_configured: boolean;
};

export type AiProbeResult = {
  ok: boolean;
  stage: "config" | "call";
  reason: string;
  model: string;
  latency_ms?: number;
};

export type DecisionRuleCheck = {
  code: string;
  label: string;
  status: "pass" | "warn" | "fail" | "unknown";
  detail: string;
};

export type DecisionAnalysis = {
  summary: string;
  confirmed_facts: { text: string; source: string }[];
  uncertainties: string[];
  direction: string;
  priority: "A" | "B" | "C" | "D" | "待确认";
  reasons: string[];
  risks: string[];
  hard_conditions: string[];
  next_action: string;
  action_text: string;
  reply_draft: string;
  pipeline_recommendation: { should_add: boolean; reason: string };
  rule_checks: DecisionRuleCheck[];
};

export type ChatThread = {
  id: number;
  kind: "general" | "job" | "ingest";
  job_id?: number | null;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message?: string | null;
  reused?: boolean;
  job?: {
    id: number;
    title: string;
    company_name: string;
    salary_text?: string | null;
    city?: string | null;
    area?: string | null;
  } | null;
};

export type ChatMessage = {
  id: number;
  thread_id: number;
  role: "user" | "assistant";
  content: string;
  metadata_json?: {
    analysis?: DecisionAnalysis;
    ai_used?: boolean;
    run_status?: "completed" | "fallback" | "rules_only";
    candidates?: IngestCandidate[];
    sources_report?: { source: string; jobs: number; skipped?: unknown[] }[];
    unmatched?: boolean;
    needs_ai?: boolean;
    attachment?: {
      kind: "image";
      id: string;
      name: string;
      mime_type: string;
      size_bytes: number;
    };
  };
  created_at: string;
};

export type IngestCandidate = {
  title: string;
  company_name: string;
  salary_text?: string | null;
  city?: string | null;
  area?: string | null;
  source?: string;
  url?: string | null;
  status?: "pending" | "committed" | "skipped";
  job_id?: number | null;
  description?: string | null;
  board_written?: boolean;
  /** 命中岗位池里已有岗位的 canonical_key 时后端会带上；仅用于展示，提交时会被后端剔除。 */
  existing_job_id?: number | null;
  /** 与最近 ~50 个 ingest 线程里出现过的候选重复（canonical_key，缺失时退化为标题+公司）时后端会带上
   * 匹配到的线程 id；仅用于展示「重复候选」徽标并默认不勾选，提交时会被后端剔除。 */
  duplicate_in_thread_id?: number | null;
};

export type ContextRepoStatus = {
  configured: boolean;
  available: boolean;
  documents: Record<string, boolean>;
  message: string;
};

export type BoardWriteResult = {
  index: number;
  ok: boolean;
  reason: string;
  skipped?: boolean;
};

export type ChatThreadDetail = {
  thread: ChatThread;
  messages: ChatMessage[];
};

export type ChatThreadBatchDeleteResult = {
  id: number;
  ok: boolean;
  reason?: string;
};

export type ChatThreadBatchDeleteReply = {
  results: ChatThreadBatchDeleteResult[];
  deleted: number;
};

export type ChatReply = {
  thread: ChatThread;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  analysis: DecisionAnalysis;
  ai_used: boolean;
};

export type ChatContextPreview = {
  ai_enabled: boolean;
  model: string | null;
  sections: { key: string; chars: number; content: string }[];
  context_chars_total: number;
  job_context: Record<string, string>;
  conversation_count: number;
  note: string;
};

export type AppConfig = {
  path: string;
  config: Record<string, unknown>;
  env: {
    openai_api_key_configured: boolean;
    openai_base_url_configured: boolean;
    openai_model: string;
    database_url_configured: boolean;
    port: number;
    max_upload_mb: number;
  };
  config_error?: string | null;
  editable_sections: string[];
  restart_recommended_after_save: string[];
};

export type JobSourceStatus = {
  key: string;
  label: string;
  kind: string;
  enabled: boolean;
  configured: boolean;
  status: string;
  message: string;
  doctor?: {
    status: string;
    configured: boolean;
    message: string;
    runtime?: string;
    recommended_path?: string | null;
  } | null;
  config: {
    command?: string[];
    role_urls?: string[];
    timeout_seconds?: number;
    host_collection?: {
      script: string;
      message: string;
    };
  };
  latest_run?: SourceRun | null;
};

export type InterviewPrep = {
  id: number;
  job_id: number;
  jd_summary: string;
  skill_gaps: string;
  resume_points: string;
  star_stories: string;
  questions_to_ask: string;
  core_pitch: string;
  communication_draft: string;
  tailored_resume: string;
  created_at: string;
};

export type Draft = {
  id: number;
  job_id?: number | null;
  kind: string;
  channel: string;
  content: string;
  status: string;
  created_at: string;
};

export type SourceRunReport = {
  urls_total?: number;
  urls_ok?: number;
  jobs?: number;
  input_links?: number;
  skipped?: { url?: string; reason?: string }[];
};

export type SourceRun = {
  id: number;
  source: string;
  status: string;
  started_at: string;
  finished_at?: string | null;
  fetched_count: number;
  created_count: number;
  updated_count: number;
  error?: string | null;
  raw_config?: SourceRunReport | null;
};

export type FollowUpTask = {
  id: number;
  job_id?: number | null;
  title: string;
  status: string;
  due_date?: string | null;
  /** 创建接口命中已有的同岗位+同标题+未完成待办时返回 true（幂等，不新建），供前端提示已存在。 */
  duplicate?: boolean;
};

export type StaleJob = {
  job_id: number;
  title: string;
  company_name: string;
  status: string;
  days: number;
  reason: string;
};

export type InterviewLog = {
  id: number;
  job_id: number;
  round: string;
  interview_date?: string | null;
  interviewer?: string | null;
  real_picture: string;
  opportunity_score?: number | null;
  conclusion: string;
  score_details: Record<string, number>;
  qa_review: string;
  weaknesses: string;
  next_actions: string;
  follow_up: string;
  created_at: string;
  updated_at: string;
};

export type FunnelAnalytics = {
  summary: {
    top_score_jobs: number;
    applied_jobs: number;
    interview_jobs: number;
    offer_jobs: number;
    stale_jobs: number;
  };
};

export type SprintBrief = {
  generated_at: string;
  profile: UserProfile;
  top_jobs: (Job & { latest_score: FitScore })[];
  prepared: { job: Job; prep: InterviewPrep }[];
  tasks_created: FollowUpTask[];
  stale_jobs: StaleJob[];
  markdown: string;
};
