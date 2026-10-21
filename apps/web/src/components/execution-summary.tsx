import type { Artifact } from "@/lib/types";

export function ExecutionSummary({ artifact }: { artifact: Artifact }) {
  const calls = (artifact.usage?.calls || []) as Record<string, unknown>[];
  const incoming = calls.reduce((n, call) => n + Number(call.input_tokens || 0), 0);
  const outgoing = calls.reduce((n, call) => n + Number(call.output_tokens || 0), 0);
  const used = new Set(artifact.observability?.used_source_ids || []);
  return <section className="execution-summary" aria-label="Latency and usage breakdown"><h3>At a glance</h3><div className="metric-grid">
    <div><strong>{artifact.observability ? `${(artifact.observability.total_ms / 1000).toFixed(1)}s` : "—"}</strong><small>Total response time</small></div>
    <div><strong>{Number(artifact.usage?.total_tokens || 0).toLocaleString()}</strong><small>Reported tokens{artifact.usage?.partial ? " · partial" : ""}</small></div>
    <div><strong>{incoming.toLocaleString()} / {outgoing.toLocaleString()}</strong><small>Input / output tokens</small></div>
    <div><strong>{calls.length}</strong><small>Model calls with usage</small></div></div>
    {artifact.usage?.usage_reported === false && <p className="muted small">Final provider usage is unavailable. A conservative quota reservation may remain.</p>}
    {!!artifact.selected_sources?.length && <><h3>Source scope</h3>{artifact.selected_sources.map(s => <p className="source-scope-row" key={s.id}><span>{s.name}</span><span className="badge">{used.has(s.id) ? "Used" : "Selected"}</span></p>)}</>}
    {artifact.observability?.public_web_used && <p className="badge">Public web evidence used</p>}
    <p className="muted small">Stage times can overlap during parallel reads. Tokens are provider-reported, not a billing estimate.</p>
  </section>;
}
