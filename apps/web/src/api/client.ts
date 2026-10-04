export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: Record<string, string>;
}

/** Calls the API under /api with the local-only development principal header. */
export async function apiRequest<T>(
  path: string,
  principalId: string,
  { method = "GET", body, headers = {} }: RequestOptions = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      "X-Dev-Principal": principalId,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText || "Request failed";
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (Array.isArray(payload.detail)) detail = "The request was rejected as invalid.";
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, humanize(detail));
  }
  return (await response.json()) as T;
}

export function humanize(code: string): string {
  return code.replaceAll("_", " ");
}

export function newIdempotencyKey(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}
