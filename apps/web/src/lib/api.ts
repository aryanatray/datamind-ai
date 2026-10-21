let csrf = "";
let workspace = typeof window === "undefined" ? "" : sessionStorage.getItem("dm_workspace") || "";
export function streamId() { return crypto.randomUUID(); }
export function credentials(token: string, workspaceId: string) { csrf = token; workspace = workspaceId; sessionStorage.setItem("dm_workspace", workspaceId); }
export function headers() { return { "X-CSRF-Token": csrf, "X-Workspace-ID": workspace }; }
export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, credentials: "same-origin", cache: "no-store", headers: { ...headers(), ...(body instanceof FormData ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ detail: "The server returned an unreadable response" }));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || "Request failed"));
  return data as T;
}
export async function streamChat(path: string, body: unknown, signal: AbortSignal, onEvent: (event: Record<string, unknown>) => void) {
  const response = await fetch(`/api${path}`, { method: "POST", credentials: "same-origin", headers: { ...headers(), "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "Request failed"); }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Streaming is unavailable");
  const decoder = new TextDecoder(); let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const packets = buffer.split("\n\n"); buffer = packets.pop() || "";
      for (const packet of packets) if (packet.startsWith("data: ")) onEvent(JSON.parse(packet.slice(6)));
    }
  } finally { reader.releaseLock(); }
}
