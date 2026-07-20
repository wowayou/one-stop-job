const API_BASE = import.meta.env.VITE_API_BASE ?? "";

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

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseApiError(response));
  }
  return response.json() as Promise<T>;
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
  const response = await fetch(`${API_BASE}${path}`, {
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

// 统一复制：优先 navigator.clipboard（需 https/localhost），失败回退 execCommand。
// 返回是否成功，调用方据此给出反馈，避免在普通 HTTP 或旧浏览器下静默失败。
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
