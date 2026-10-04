const BASE_URL = "/api";

/** RFC 9457 problem details. */
export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  /** ASP.NET Core model validation puts field errors here; mirror it server-side in Python. */
  errors?: Record<string, string[]>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetails;

  constructor(status: number, problem: ProblemDetails) {
    super(problem.detail ?? problem.title ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }

  /** Field-level validation messages, flattened for react-hook-form. */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [field, messages] of Object.entries(this.problem.errors ?? {})) {
      const first = messages[0];
      if (first !== undefined) out[field] = first;
    }
    return out;
  }

  /** Retrying a 4xx will fail the same way. Used by the shared QueryClient. */
  get isRetryable(): boolean {
    return this.status >= 500 || this.status === 408 || this.status === 429;
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  /** Serialized as JSON. Omit for GET and DELETE. */
  body?: unknown;
  /** Appended as a query string; undefined and null values are dropped. */
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/**
 * `HeadersInit` is a union: a record, a `Headers` instance, or an array of
 * pairs. Only the record survives object spread — a `Headers` instance spreads
 * to nothing (its entries are not own enumerable properties) and an array
 * spreads to `{0: [...], 1: [...]}`. Both are silent, so build a real `Headers`
 * instead of merging object literals.
 */
function buildHeaders(headers: HeadersInit | undefined, hasBody: boolean): Headers {
  const merged = new Headers({ Accept: "application/json" });
  if (hasBody) merged.set("Content-Type", "application/json");
  new Headers(headers).forEach((value, key) => merged.set(key, value));
  return merged;
}

/**
 * 204 and 205 are defined as bodiless, and a server is free to answer with an
 * empty body on other statuses too. Calling `response.json()` on any of those
 * throws a SyntaxError, which would escape this module as something other than
 * an ApiError and defeat the single-error-type contract.
 */
async function parseBody<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.status === 205) return undefined as T;
  const text = await response.text();
  if (text === "") return undefined as T;
  return JSON.parse(text) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  let problem: ProblemDetails = {};
  try {
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("json")) {
      problem = (await response.json()) as ProblemDetails;
    }
  } catch {
    // A body that will not parse is not worth a second failure mode.
  }
  return new ApiError(response.status, {
    title: response.statusText,
    status: response.status,
    ...problem,
  });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, headers, ...init } = options;

  const response = await fetch(buildUrl(path, query), {
    ...init,
    headers: buildHeaders(headers, body !== undefined),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (!response.ok) throw await toApiError(response);

  return parseBody<T>(response);
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, "body" | "method">) =>
    request<T>(path, { ...options, method: "GET" }),

  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body" | "method">) =>
    request<T>(path, { ...options, method: "POST", body }),

  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body" | "method">) =>
    request<T>(path, { ...options, method: "PUT", body }),

  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body" | "method">) =>
    request<T>(path, { ...options, method: "PATCH", body }),

  delete: <T>(path: string, options?: Omit<RequestOptions, "body" | "method">) =>
    request<T>(path, { ...options, method: "DELETE" }),
};
