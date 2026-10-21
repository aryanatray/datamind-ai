"use client";
import { useEffect, useRef, useState } from "react";
import { Database, FileText, Plus, Upload, RefreshCw, Trash2, Shield, Search, CheckCircle2, Unplug } from "lucide-react";
import { api } from "@/lib/api";
import type { Result, Source } from "@/lib/types";
import { Analytics } from "./analytics";
import { Empty, ErrorBox, Field, Modal } from "./primitives";

type Preview = { filename: string; status: string; collection: string; version: number; versions: {version: number; filename: string; size: number; created: string; sha256: string}[]; extracted: { text: string; page?: number }[]; profile: Record<string, unknown>; jobs: { state: string; stage: string; error: string; version: number }[] };
type Member = { id: string; name: string; email: string; role: string };

export function Sources({ sources, documents, isAdmin, refresh, onAsk, report }: { sources: Source[]; documents: boolean; isAdmin: boolean; refresh: () => Promise<void>; onAsk: (id: string) => void; report: (error: string) => void }) {
  const [filter, setFilter] = useState(""); const [adding, setAdding] = useState(false); const [editing, setEditing] = useState<Source | null>(null);
  const [kind, setKind] = useState("postgres"); const [active, setActive] = useState<Source | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null); const [schema, setSchema] = useState<Record<string, unknown>>({});
  const [resources, setResources] = useState<string[]>([]); const [description, setDescription] = useState(""); const [metrics, setMetrics] = useState("{}");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [sql, setSql] = useState(""); const [result, setResult] = useState<Result | null>(null);
  const [members, setMembers] = useState<Member[]>([]); const [granted, setGranted] = useState<string[]>([]); const [permissionsOpen, setPermissionsOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Source | null>(null); const fileInput = useRef<HTMLInputElement>(null);
  const replacementInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!active || !preview || !["queued", "running"].includes(preview.status)) return;
    let pending = false; let alive = true;
    const timer = setInterval(async () => {
      if (pending) return; pending = true;
      try { const next = await api<Preview>(`/documents/${active.id}`); if (alive) setPreview(next); }
      catch (e) { if (alive) setError((e as Error).message); }
      finally { pending = false; }
    }, 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [active, preview]);
  async function replaceFile(file: File) {
    if (!active) return;
    await perform(async () => { const body = new FormData(); body.append("file", file);
      await api(`/documents/${active.id}/replace`, "POST", body);
      setPreview(await api<Preview>(`/documents/${active.id}`)); setResult(null); setSchema({});
    });
  }
  const visible = sources.filter(s => (documents ? ["document", "table"].includes(s.kind) : !["document", "table"].includes(s.kind)) && s.name.toLowerCase().includes(filter.toLowerCase()));
  async function upload(files: FileList | File[]) {
    setBusy(true); setError("");
    try { for (const file of Array.from(files)) { const body = new FormData(); body.append("file", file); await api("/documents", "POST", body); } await refresh(); }
    catch (e) { report((e as Error).message); } finally { setBusy(false); }
  }
  async function open(source: Source) {
    setActive(source); setError(""); setResult(null); setPreview(null); setSql(""); setResources(source.resources);
    setSchema(source.catalog.schema || {}); setDescription(source.catalog.description || ""); setMetrics(JSON.stringify(source.catalog.metrics || {}, null, 2));
    try { if (["document", "table"].includes(source.kind)) setPreview(await api<Preview>(`/documents/${source.id}`)); } catch (e) { setError((e as Error).message); }
  }
  async function perform(action: () => Promise<unknown>) { setBusy(true); setError(""); try { await action(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function connect(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const body = { name: form.get("name"), kind, config: { host: form.get("host") || "localhost", port: Number(form.get("port") || 5432), database: form.get("database") || "", username: form.get("username") || "", password: form.get("password") || "", path: form.get("path") || "", tls: form.get("tls") === "on", auth_database: form.get("auth_database") || "admin" }, resources: [], catalog: { description: form.get("description") || "" } };
    await perform(async () => { const saved = await api<Source>(editing ? `/connections/${editing.id}` : "/connections", editing ? "PUT" : "POST", body); setAdding(false); setEditing(null); await open(saved); });
  }
  async function test() { if (!active) return; await perform(async () => { const data = await api<{ schema: Record<string, unknown> }>(`/connections/${active.id}/test`, "POST"); setSchema(data.schema); }); }
  async function saveCatalog() { if (!active) return; await perform(async () => {
    const metricValues = JSON.parse(metrics); if (Array.isArray(metricValues) || typeof metricValues !== "object" || metricValues === null) throw new Error("Metrics must be a JSON object");
    const saved = await api<Source>(`/sources/${active.id}/catalog`, "PATCH", { resources, catalog: { ...active.catalog, description, metrics: metricValues } }); setActive(saved);
  }); }
  async function showPermissions() { if (!active) return; await perform(async () => { const [team, grants] = await Promise.all([api<{ members: Member[] }>("/workspace"), api<string[]>(`/sources/${active.id}/permissions`)]); setMembers(team.members); setGranted(grants); setPermissionsOpen(true); }); }
  async function changePermission(member: Member, allowed: boolean) {
    const previous = granted;
    setGranted(allowed ? [...granted, member.id] : granted.filter(id => id !== member.id));
    await perform(async () => { try { await api(`/sources/${active?.id}/permissions`, "PUT", { user_id: member.id, allowed }); } catch (error) { setGranted(previous); throw error; } });
  }
  return <section className="page" onDragOver={e => { if (documents && isAdmin) e.preventDefault(); }} onDrop={e => { if (documents && isAdmin) { e.preventDefault(); void upload(e.dataTransfer.files); } }}>
    <div className="page-title"><div><span className="eyebrow">WORKSPACE SOURCES</span><h1>{documents ? "Document library" : "Data connections"}</h1><p>{documents ? "A shared home for the evidence behind every answer." : "Connect your systems. Keep control of every query."}</p></div>{isAdmin && <button className="primary" onClick={() => documents ? fileInput.current?.click() : (setEditing(null), setAdding(true), setError(""))}>{documents ? <Upload size={17}/> : <Plus size={17}/>} {documents ? "Upload documents" : "Add connection"}</button>}</div>
    <input ref={fileInput} type="file" className="sr-only" aria-label="Upload documents" multiple accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.json" onChange={e => { if (e.target.files) void upload(e.target.files); e.target.value = ""; }}/>
    <input ref={replacementInput} type="file" className="sr-only" aria-label="Replace document file" accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.json" onChange={e => { if (e.target.files?.[0]) void replaceFile(e.target.files[0]); e.target.value = ""; }}/>
    <div className="search-field"><Search size={17}/><input aria-label="Search sources" placeholder={documents ? "Find a document…" : "Find a connection…"} value={filter} onChange={e => setFilter(e.target.value)}/><span>{visible.length} sources</span></div>
    {busy && <p role="status">Processing request…</p>}
    {visible.length ? <div className="source-grid">{visible.map(source => <button key={source.id} className="source-card" onClick={() => void open(source)}><div className="source-icon">{documents ? <FileText size={22}/> : <Database size={22}/>}</div><h3>{source.name}</h3><p>{source.kind.toUpperCase()} · {source.resources.length} resources</p><span className={`status ${source.status === "ready" ? "ready" : ""}`}><span/>{source.status.replaceAll("_", " ")}</span></button>)}</div> : <Empty title={documents ? "Start with a document" : "Bring your data together"} text={documents ? "Upload PDF, Word, text, Markdown, CSV, Excel or JSON. You can also drop files here." : "Connect PostgreSQL, MySQL, SQLite or MongoDB to ask questions against real data."}/>}
    <Modal title={editing ? "Update connection" : "New data connection"} open={adding} onClose={() => { setAdding(false); setError(""); }}><form onSubmit={connect}>
      <Field label="Connection name"><input name="name" required defaultValue={editing?.name}/></Field><Field label="Database type"><select value={kind} onChange={e => setKind(e.target.value)}><option value="postgres">PostgreSQL</option><option value="mysql">MySQL</option><option value="sqlite">SQLite</option><option value="mongo">MongoDB</option></select></Field>
      {kind === "sqlite" ? <Field label="File inside the configured SQLite source directory"><input name="path" placeholder="aster.db" required/></Field> : <><div className="two-columns"><Field label="Host"><input name="host" defaultValue="localhost" required/></Field><Field label="Port"><input name="port" type="number" key={kind} defaultValue={kind === "postgres" ? 5432 : kind === "mysql" ? 3306 : 27017} required/></Field></div><Field label="Database"><input name="database" required/></Field><div className="two-columns"><Field label="Read-only username"><input name="username" required autoComplete="off"/></Field><Field label="Password"><input name="password" type="password" required autoComplete="off"/></Field></div>{kind === "mongo" && <Field label="Authentication database"><input name="auth_database" defaultValue="admin"/></Field>}<label className="checkbox"><input type="checkbox" name="tls"/> Require TLS</label></>}
      <Field label="Purpose and business context"><textarea name="description" defaultValue={editing?.catalog.description}/></Field><p className="muted small">Use an account with read-only grants. The platform operator must allow the database host. Credentials stay encrypted on the server.</p><ErrorBox error={error}/><button className="primary full" disabled={busy}>Save connection</button>
    </form></Modal>
    <Modal title={active?.name || "Source"} open={!!active} onClose={() => { setActive(null); setError(""); }}>
      {active && <><div className="row wrap"><span className="badge">{active.kind}</span><button onClick={() => onAsk(active.id)}>Ask about this source</button>{isAdmin && <><button onClick={() => void showPermissions()}><Shield size={15}/> Permissions</button>{!["document", "table"].includes(active.kind) && <><button onClick={() => void test()}><CheckCircle2 size={15}/> Test & discover</button><button onClick={() => { setEditing(active); setKind(active.kind); setAdding(true); setActive(null); }}>Update credentials</button><button onClick={() => void perform(async () => { await api(`/sources/${active.id}/disconnect`, "POST"); setActive(null); })}><Unplug size={15}/> Disconnect</button></>}{["document", "table"].includes(active.kind) && <button onClick={() => void perform(async () => { await api(`/documents/${active.id}/reindex`, "POST"); setActive(null); })}><RefreshCw size={15}/> Reindex</button>}<button className="danger" onClick={() => setDeleteTarget(active)}><Trash2 size={15}/> Delete</button></>}</div>
      {preview && <><p className="muted">Index version {preview.version} · {preview.status} · Collection: {preview.collection}</p>{isAdmin && <form className="row" onSubmit={e => { e.preventDefault(); const name = new FormData(e.currentTarget).get("collection"); void perform(async () => { await api(`/documents/${active.id}/collection`, "PATCH", { name, capacity: 1 }); setPreview({ ...preview, collection: String(name) }); }); }}><input name="collection" aria-label="Collection" defaultValue={preview.collection} required maxLength={100}/><button>Set collection</button></form>}<section className="processing-panel" aria-label="Document processing"><h3>Processing</h3><div className="processing-steps">{["Queued", "Extract", "Chunk", "Index", "Ready"].map((label, i) => { const stage = preview.jobs[0]?.stage; const current = preview.status === "ready" ? 4 : stage === "embedding_and_indexing" ? 3 : stage === "chunking" ? 2 : stage === "extracting" || stage === "parsing" ? 1 : 0; return <span key={label} className={i <= current ? "done" : ""}>{label}</span>; })}</div>{preview.status === "failed" && <p className="error">Processing failed. Review the error below, then use Reindex or replace the file.</p>}<p className="muted small">{preview.profile.chunks ? `${preview.profile.chunks} searchable chunks` : preview.status === "ready" ? "Queryable table is ready" : "Progress follows actual worker stages. Model initialization can take time."}</p></section><details><summary>File version history ({preview.versions.length})</summary><p className="muted small">Replacement preserves the source and grants. Old answers become unavailable when the index version changes. Restoring creates a new version.</p>{isAdmin && <button disabled={busy} onClick={() => replacementInput.current?.click()}>Replace file</button>}{preview.versions.map(version => <div className="version-row" key={version.version}><div><strong>Version {version.version} · {version.filename}</strong><small>{new Date(version.created + "Z").toLocaleString()} · {(version.size / 1024).toFixed(1)} KB · {version.sha256.slice(0, 12)}</small></div><a href={`/api/documents/${active.id}/versions/${version.version}/download`}>Download</a>{isAdmin && <button disabled={busy} onClick={() => void perform(async () => { await api(`/documents/${active.id}/versions/${version.version}/restore`, "POST"); setPreview(await api<Preview>(`/documents/${active.id}`)); setResult(null); setSchema({}); })}>Restore v{version.version}</button>}</div>)}</details>{preview.jobs.map((job, i) => <div key={i} className={job.error ? "error" : "muted small"}>{job.stage}{job.error ? ` · ${job.error}` : ""}</div>)}<div className="document-preview">{preview.extracted.map((block, i) => <div key={i}>{block.page && <small>PAGE {block.page}</small>}<p>{block.text}</p></div>)}</div><details><summary>Profile and extracted metadata</summary><pre>{JSON.stringify(preview.profile, null, 2)}</pre></details></>}
      {Object.keys(schema).length > 0 && <><h3>Resources and semantic catalog</h3><p className="muted small">Only selected resources can be queried. Describe metrics explicitly to avoid ambiguous calculations.</p><div className="resource-list">{Object.keys(schema).map(table => <label className="checkbox" key={table}><input type="checkbox" disabled={!isAdmin} checked={resources.includes(table)} onChange={e => setResources(e.target.checked ? [...resources, table] : resources.filter(t => t !== table))}/>{table}</label>)}</div><Field label="Description"><textarea value={description} onChange={e => setDescription(e.target.value)} readOnly={!isAdmin}/></Field><Field label="Business metric definitions (JSON)"><textarea className="code-input" value={metrics} onChange={e => setMetrics(e.target.value)} readOnly={!isAdmin}/></Field>{isAdmin && <button className="primary" disabled={busy} onClick={() => void saveCatalog()}>Save resources and definitions</button>}<details><summary>Inspect discovered schema</summary><pre>{JSON.stringify(schema, null, 2)}</pre></details></>}
      {active.kind !== "document" && <><h3>Read-only query inspector</h3><Field label={active.kind === "mongo" ? "MongoDB query specification (JSON)" : "SQL query"}><textarea className="code-input" value={sql} onChange={e => setSql(e.target.value)} placeholder={active.kind === "mongo" ? '{"collection":"tickets","filter":{"status":"open"},"limit":50}' : "SELECT * FROM an_allowed_table LIMIT 20"}/></Field><button disabled={busy || !sql} onClick={() => void perform(async () => setResult(await api<Result>(`/sources/${active.id}/${active.kind === "mongo" ? "mongo" : "sql"}`, "POST", active.kind === "mongo" ? JSON.parse(sql) : { sql })))}>Run read-only query</button>{result && <Analytics result={result}/>}</>}
      <ErrorBox error={error}/></>}
    </Modal>
    <Modal title="Source permissions" open={permissionsOpen} onClose={() => setPermissionsOpen(false)}><p className="muted small">Administrators can access all workspace sources. Members need explicit grants. Revocation also hides saved results and chat evidence.</p>{members.map(member => <label className="permission-row" key={member.id}><span>{member.name}<small>{member.role} · {member.email}</small></span><input aria-label={`Allow ${member.name} (${member.email})`} type="checkbox" checked={member.role === "admin" || granted.includes(member.id)} disabled={member.role === "admin" || busy} onChange={e => void changePermission(member, e.target.checked)}/></label>)}<ErrorBox error={error}/></Modal>
    <Modal title="Delete this source?" open={!!deleteTarget} onClose={() => setDeleteTarget(null)}><p>DataMind will remove access and indexed content for {deleteTarget?.name}. The connected external database is unaffected.</p><button className="danger" disabled={busy} onClick={() => void perform(async () => { await api(`/sources/${deleteTarget?.id}`, "DELETE"); setDeleteTarget(null); setActive(null); })}>Delete source</button><ErrorBox error={error}/></Modal>
  </section>;
}
