export class ConsoleApiError extends Error {
  constructor(
    public code: string,
    public retryable: boolean,
  ) {
    super(code);
  }
}
export async function getJson<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/console/v1${path}`, {
      signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ConsoleApiError("connection_lost", true);
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new ConsoleApiError("read_failed", response.status >= 500);
  }
  if (!response.ok)
    throw new ConsoleApiError(
      body.error?.code ?? "read_failed",
      body.error?.retryable ?? false,
    );
  return body as T;
}
export const params = (values: Record<string, string | undefined>) => {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== undefined && value !== "") result.set(key, value);
  return result;
};
