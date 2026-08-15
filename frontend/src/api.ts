// 后端固定在 127.0.0.1:8000。
// - 生产模式（后端挂载前端 :8000）：同源，API_BASE 为空
// - 其他模式（Vite dev / Tauri dev / Tauri prod）：直接指向 :8000
// - 自定义：通过 VITE_API_BASE 环境变量覆盖
function detectApiBase(): string {
  const envBase = import.meta.env.VITE_API_BASE;
  if (envBase) return envBase;
  // 后端挂载模式（scripts/app.sh）：同源不需要前缀
  if (typeof window !== "undefined" && window.location.port === "8000") return "";
  // 开发模式和 Tauri 模式：直接指向后端
  return "http://127.0.0.1:8000";
}
const API_BASE = detectApiBase();

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
  };
  detail?: string;
};

type ParsedApiError = {
  code?: string;
  details?: unknown;
  message: string;
  requestId?: string;
};

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: unknown;
  requestId?: string;

  constructor(status: number, payload: ParsedApiError) {
    super(payload.requestId ? `${payload.message}（request_id: ${payload.requestId}）` : payload.message);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code;
    this.details = payload.details;
    this.requestId = payload.requestId;
  }
}

async function parseApiError(response: Response): Promise<ParsedApiError> {
  const text = await response.text();
  if (!text) return { message: response.statusText || "请求失败" };
  try {
    const payload = JSON.parse(text) as ApiErrorPayload;
    if (payload.error?.message) {
      return {
        code: payload.error.code,
        details: payload.error.details,
        message: payload.error.message,
        requestId: payload.error.request_id,
      };
    }
    if (typeof payload.detail === "string") return { message: payload.detail };
  } catch {
    // fall through to raw text
  }
  return { message: text };
}

const DEFAULT_TIMEOUT_MS = 30000;

/** fetch with timeout: 超时自动 abort，避免 UI 永久等待后端响应。 */
async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, { message: `请求超时（${timeoutMs / 1000}s）` });
    }
    throw new ApiError(0, { message: err instanceof Error ? err.message : "网络请求失败" });
  } finally {
    clearTimeout(timer);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseApiError(response));
  }
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(response.status, { message: "响应解析失败：服务器返回了非 JSON 内容" });
  }
}

export function errorMessage(err: unknown, fallback: string) {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function filenameFromDisposition(value: string | null, fallback: string) {
  if (!value) return fallback;
  const match = /filename="?([^"]+)"?/.exec(value);
  return match?.[1] ?? fallback;
}

export async function downloadApiFile(path: string, fallbackName: string, init?: RequestInit): Promise<string> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseApiError(response));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const filename = filenameFromDisposition(response.headers.get("Content-Disposition"), fallbackName);
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return filename;
}

export function jsonBody(payload: unknown): RequestInit {
  return { body: JSON.stringify(payload) };
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to execCommand fallback
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}
