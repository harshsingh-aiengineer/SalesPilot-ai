export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const base = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export function getToken(): string | null {
  return localStorage.getItem("salespilot_token");
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem("salespilot_token", token);
  else localStorage.removeItem("salespilot_token");
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${base}${path}`, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail;
    const message = detail && typeof detail === "object" && detail.message ? String(detail.message) : `Request failed (${response.status}).`;
    const code = detail && typeof detail === "object" && detail.code ? String(detail.code) : "http_error";
    if (response.status === 401 && !path.startsWith("/auth/login")) {
      setToken(null);
      if (!window.location.pathname.startsWith("/login")) window.location.assign("/login");
    }
    throw new ApiError(response.status, code, message);
  }
  return payload as T;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
