"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, ChartNoAxesCombined, Check, ChevronDown, Copy, Database, FileText, Globe, LayoutDashboard, Menu, Network, PanelLeftClose, PanelRightClose, Paperclip, Pin, Plus, RefreshCw, Search, Settings2, ShieldCheck, Square, Trash2, UserRound, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, credentials, streamId, streamChat } from "@/lib/api";
import type { Artifact, ChartSpec, Dashboard, Me, Message, Source } from "@/lib/types";
import { Analytics } from "@/components/analytics";
import { Auth } from "@/components/auth";
import { Insights } from "@/components/insights";
import { ErrorBox, Field, Modal } from "@/components/primitives";
import { Settings } from "@/components/settings";
import { Setup } from "@/components/setup";
import { ExecutionSummary } from "@/components/execution-summary";
import { Sources } from "@/components/sources";

type Conversation = { id: string; title: string; pinned?: boolean; scope?: {source_ids?: string[]; web_enabled?: boolean} };
type View = "chat" | "documents" | "connections" | "insights" | "workspace" | "account" | "setup";

export default function Home() {
  const [me, setMe] = useState<Me | null>(null); const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("chat"); const [sidebar, setSidebar] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]); const [chatId, setChatId] = useState(""); const [search, setSearch] = useState("");
  const [sources, setSources] = useState<Source[]>([]); const [customSelected, setSelected] = useState<string[]>([]); const [sourcePicker, setSourcePicker] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]); const [question, setQuestion] = useState(""); const [busy, setBusy] = useState(false); const [stage, setStage] = useState("");
  const [error, setError] = useState(""); const [inspector, setInspector] = useState<Message | null>(null); const [webEnabled, setWebEnabled] = useState(true); const [scopeMode, setScopeMode] = useState<"all" | "custom">("all");
  const [saving, setSaving] = useState<{ message: Message; chart: ChartSpec } | null>(null); const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [copied, setCopied] = useState(""); const [deleting, setDeleting] = useState<Conversation | null>(null);
  const controller = useRef<AbortController | null>(null); const end = useRef<HTMLDivElement>(null); const attachment = useRef<HTMLInputElement>(null);
  const readyIds = sources.filter(s => s.status === "ready").map(s => s.id);
  const selected = scopeMode === "all" && !chatId ? readyIds : customSelected.filter(id => readyIds.includes(id));
  const report = useCallback((text: string) => setError(text), []);
  const refresh = useCallback(async () => { setSources(await api<Source[]>("/sources")); }, []);
  const refreshChats = useCallback(async () => { setConversations(await api<Conversation[]>("/conversations")); }, []);
  const loadMe = useCallback(async () => {
    const data = await api<Me>("/me"); credentials(data.csrf, data.workspace_id); setMe(data);
    const appearance = data.preferences.appearance || "system";
    document.documentElement.dataset.theme = appearance === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : appearance;
  }, []);
  useEffect(() => { Promise.resolve().then(loadMe).catch(() => setMe(null)).finally(() => setLoading(false)); }, [loadMe]);
  useEffect(() => { if (me) { void Promise.resolve().then(() => Promise.all([refresh(), refreshChats()])).catch(e => report(e.message)); } }, [me?.workspace_id, view, refresh, refreshChats, report]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!me) return;
    const invitation = new URLSearchParams(location.search).get("invite");
    if (invitation) void api<{ workspace_id: string }>("/workspace/accept", "POST", { token: invitation }).then(async data => { credentials(me.csrf, data.workspace_id); history.replaceState({}, "", "/"); await loadMe(); }).catch(e => report(e.message));
  }, [me?.id, loadMe, report]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (sources.some(s => ["queued", "running"].includes(s.status))) { const timer = setInterval(() => void refresh().catch(e => report(e.message)), 3000); return () => clearInterval(timer); } }, [sources, refresh, report]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, stage]);
  // Revalidate visible persisted artifacts on focus. Server authorization is still mandatory on every action.
  useEffect(() => {
    async function revalidate() { if (!chatId || busy) return; try { setMessages(await api<Message[]>(`/conversations/${chatId}`)); setInspector(null); } catch (e) { report((e as Error).message); } }
    window.addEventListener("focus", revalidate); return () => window.removeEventListener("focus", revalidate);
  }, [chatId, busy, report]);
  async function switchWorkspace(id: string) { controller.current?.abort(); if (!me) return; credentials(me.csrf, id); setScopeMode("all"); setSources([]); setChatId(""); setMessages([]); setSelected([]); setInspector(null); await loadMe(); }
  async function logout() { controller.current?.abort(); await api("/auth/logout", "POST"); newChat(); setSelected([]); setWebEnabled(true); setMe(null); setMessages([]); setConversations([]); setSources([]); setScopeMode("all"); credentials("", ""); }
  function newChat(scope?: string[]) { setScopeMode(scope ? "custom" : "all"); setSelected(scope || sources.filter(s => s.status === "ready").map(s => s.id)); setWebEnabled(true); controller.current?.abort(); setBusy(false); setChatId(""); setMessages([]); setQuestion(""); setInspector(null); setView("chat"); setError(""); }
  async function openChat(chat: Conversation) { if (busy) controller.current?.abort(); setBusy(false); setError(""); setView("chat"); setChatId(chat.id); setInspector(null); setScopeMode("custom"); setSelected(chat.scope?.source_ids || []); setWebEnabled(chat.scope?.web_enabled ?? true); try { setMessages(await api<Message[]>(`/conversations/${chat.id}`)); } catch (e) { report((e as Error).message); } }
  async function send(text = question, regenerate = false) {
    if (!text.trim() || busy) return;
    setScopeMode("custom"); setSelected(selected); setBusy(true); setError(""); setQuestion(""); setView("chat"); setStage("Starting"); setInspector(null);
    const abort = new AbortController(); controller.current = abort;
    let id = chatId; const tempId = `stream-${streamId()}`;
    try {
      if (!id) { const chat = await api<Conversation>("/conversations", "POST"); id = chat.id; setChatId(id); }
      setMessages(previous => [...previous, ...(!regenerate ? [{ id: tempId + "-user", role: "user", text, artifact: {} }] : []), { id: tempId, role: "assistant", text: "", artifact: {} }]);
      await streamChat(`/conversations/${id}/chat`, { question: text, source_ids: selected, web_enabled: webEnabled, regenerate }, abort.signal, event => {
        if (event.type === "stage") setStage(String(event.stage).replaceAll("_", " "));
        if (event.type === "delta") setMessages(previous => previous.map(m => m.id === tempId ? { ...m, text: m.text + String(event.text) } : m));
        if (event.type === "error") { setMessages(previous => [...previous.filter(m => m.id !== tempId), { id: String(event.id || tempId + "-error"), role: "error", text: String(event.error), artifact: (event.artifact || {}) as Artifact }]); throw new Error(String(event.error)); }
        if (event.type === "done") setMessages(previous => previous.map(m => m.id === tempId ? { id: String(event.id), role: "assistant", text: String(event.text), artifact: event.artifact as Artifact } : m));
      });
      await refreshChats();
    } catch (e) {
      setMessages(previous => previous.filter(m => m.id !== tempId));
      if ((e as Error).name !== "AbortError") report((e as Error).message);
    } finally { if (controller.current === abort) { setBusy(false); setStage(""); } }
  }
  async function saveScope() { try { if (chatId) await api(`/conversations/${chatId}`, "PATCH", { source_ids: selected, web_enabled: webEnabled }); setSourcePicker(false); await refreshChats(); } catch (e) { report((e as Error).message); } }
  async function toggleWeb() { const enabled = !webEnabled; try { if (chatId) await api(`/conversations/${chatId}`, "PATCH", {web_enabled: enabled}); setWebEnabled(enabled); await refreshChats(); } catch (e) { report((e as Error).message); } }
  async function pin(chat: Conversation) { try { await api(`/conversations/${chat.id}`, "PATCH", {pinned: !chat.pinned}); await refreshChats(); } catch (e) { report((e as Error).message); } }
  async function upload(files: FileList) { try { for (const file of Array.from(files)) { const body = new FormData(); body.append("file", file); const added = await api<Source>("/documents", "POST", body); setSelected(previous => [...previous, added.id]); } await refresh(); setView("documents"); } catch (e) { report((e as Error).message); } }
  async function saveInsight(message: Message, chart: ChartSpec) { try { setDashboards(await api<Dashboard[]>("/dashboards")); setSaving({ message, chart }); } catch (e) { report((e as Error).message); } }
  async function saveWidget(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!saving) return; const form = new FormData(event.currentTarget); const dashboardId = String(form.get("dashboard"));
    const widget = { message_id: saving.message.id, result_index: saving.chart.result_index, chart: saving.chart };
    try { if (dashboardId) { const dashboard = dashboards.find(d => d.id === dashboardId)!; await api(`/dashboards/${dashboardId}`, "PUT", { name: dashboard.name, widgets: [...dashboard.widgets.map(w => ({ message_id: w.message_id, result_index: w.result_index, chart: w.chart })), widget] }); } else await api("/dashboards", "POST", { name: form.get("name") || "My insights", widgets: [widget] }); setSaving(null); setView("insights"); } catch (e) { report((e as Error).message); }
  }
  async function copy(message: Message) { try { await navigator.clipboard.writeText(message.text); setCopied(message.id); setTimeout(() => setCopied(""), 1500); } catch { report("Clipboard access is unavailable in this browser."); } }
  if (loading) return <main className="loading"><Network size={28}/><p>Opening your workspace…</p></main>;
  if (!me) return <Auth onLogin={loadMe}/>;
  const workspace = me.workspaces.find(w => w.id === me.workspace_id);
  const navigation = [{ id: "setup", title: "Setup checklist", icon: Check }, { id: "documents", title: "Document library", icon: FileText }, { id: "connections", title: "Data connections", icon: Database }, { id: "insights", title: "Insights studio", icon: LayoutDashboard }] as const;
  return <div className={`app-shell ${sidebar ? "" : "sidebar-collapsed"} ${inspector ? "has-inspector" : ""}`}>
    <aside className="sidebar" aria-label="Main navigation"><div className="brand"><Network size={26}/><strong>DataMind<span>.AI</span></strong><button className="icon-button" aria-label="Collapse sidebar" onClick={() => setSidebar(false)}><PanelLeftClose size={18}/></button></div>
      <button className="new-chat" aria-label="New conversation" onClick={() => newChat()}><Plus size={18}/> New conversation <kbd>+</kbd></button>
      <div className="sidebar-label">WORKSPACE</div><nav>{navigation.map(item => <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => { setView(item.id); setInspector(null); }}><item.icon size={18}/>{item.title}</button>)}</nav>
      <div className="sidebar-label chat-label">CONVERSATIONS <span>{conversations.length}</span></div><div className="chat-search"><Search size={14}/><input aria-label="Search chats" placeholder="Search chats" value={search} onChange={e => setSearch(e.target.value)}/></div>
      <div className="conversation-list">{conversations.filter(c => c.title.toLowerCase().includes(search.toLowerCase())).map(chat => <div className={`conversation ${chatId === chat.id && view === "chat" ? "active" : ""}`} key={chat.id}><button title={chat.title} onClick={() => void openChat(chat)}>{chat.title}</button><button className={`icon-button pin-chat ${chat.pinned ? "is-pinned" : ""}`} aria-label={`${chat.pinned ? "Unpin" : "Pin"} ${chat.title}`} aria-pressed={!!chat.pinned} onClick={() => void pin(chat)}><Pin size={13}/></button><button className="icon-button delete-chat" aria-label={`Delete ${chat.title}`} onClick={() => setDeleting(chat)}><Trash2 size={13}/></button></div>)}{!conversations.length && <p className="sidebar-empty">Your conversations will appear here.</p>}</div>
      <div className="sidebar-bottom"><button className={view === "workspace" ? "active" : ""} onClick={() => setView("workspace")}><Settings2 size={17}/> Workspace settings</button><button className="user-menu" onClick={() => setView("account")}><span className="avatar">{me.name[0]}</span><span><strong>{me.name}</strong><small>{workspace?.name}</small></span><ChevronDown size={15}/></button></div>
    </aside>
    <main className="main"><header className="topbar"><div className="row">{!sidebar && <button className="icon-button" aria-label="Expand sidebar" onClick={() => setSidebar(true)}><Menu size={20}/></button>}<span className="breadcrumb">{view === "chat" ? "Workspace" : "DataMind"}<span>/</span><strong>{view === "chat" ? conversations.find(c => c.id === chatId)?.title || "New conversation" : view === "account" ? "Account" : view === "workspace" ? "Settings" : navigation.find(n => n.id === view)?.title}</strong></span></div><div className="row"><span className="private-badge"><ShieldCheck size={13}/> Private workspace</span><select className="workspace-select" aria-label="Switch workspace" value={me.workspace_id} onChange={e => void switchWorkspace(e.target.value).catch(e => report(e.message))}>{me.workspaces.map(w => <option value={w.id} key={w.id}>{w.name}</option>)}</select></div></header>
      {error && <div className="global-error"><ErrorBox error={error}/><button className="icon-button" aria-label="Dismiss error" onClick={() => setError("")}><X size={17}/></button></div>}
      {view === "chat" ? <div className={`chat-area ${!messages.length ? "welcome-mode" : ""}`}>
        {!messages.length ? <motion.section className="welcome" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}><div className="welcome-symbol"><Network size={30} strokeWidth={1.5}/></div><span className="eyebrow">A LITTLE CLARITY GOES A LONG WAY</span><h1>What can your data<br/><span>tell you today?</span></h1><p>Connect the dots across your documents and databases.<br/>Ask naturally. Explore with evidence.</p></motion.section> : <div className="messages" aria-live="polite">{messages.map((message, index) => <article key={message.id} className={`message ${message.role}`}><div className="message-avatar">{message.role === "user" ? <UserRound size={17}/> : <Network size={20}/>}</div><div className="message-body"><div className="message-label">{message.role === "user" ? me.name : "DataMind"}</div><div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => <a href={href} onClick={event => { if (href?.startsWith("#evidence-")) { event.preventDefault(); setInspector(message); } }} target={href?.startsWith("#evidence-") ? undefined : "_blank"} rel="noreferrer noopener">{children}</a> }}>{(message.text || (busy ? "Working with your sources…" : "")).replace(/\[((?:D|R|W):[^\]\s]+)\]/g, "[$1](#evidence-$1)")}</ReactMarkdown></div>{message.artifact.results?.map((result, i) => <Analytics key={i} result={result} initial={message.artifact.charts?.find(c => c.result_index === i)} onSave={chart => void saveInsight(message, { ...chart, result_index: i })} exportUrl={`/api/messages/${message.id}/export?result_index=${i}`}/>)}{message.role !== "user" && message.text && !message.restricted && <div className="message-actions"><button aria-label="Copy response" onClick={() => void copy(message)}>{copied === message.id ? <Check size={14}/> : <Copy size={14}/>}</button>{index === messages.length - 1 && <button aria-label="Regenerate answer" disabled={busy} onClick={() => void send([...messages].reverse().find(m => m.role === "user")?.text || "", true)}><RefreshCw size={14}/></button>}{Object.keys(message.artifact).length > 0 && <button onClick={() => setInspector(message)}><ShieldCheck size={14}/> Sources & execution</button>}</div>}</div></article>)}<div ref={end}/></div>}
        <div className="composer-wrap"><form className="composer" onSubmit={e => { e.preventDefault(); void send(); }}><textarea aria-label="Ask DataMind" placeholder="Ask a question about your data…" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} rows={2} maxLength={8000}/><div className="composer-controls"><div className="row"><button type="button" className="icon-button" aria-label="Attach a file" disabled={me.role !== "admin"} onClick={() => attachment.current?.click()}><Paperclip size={18}/></button><button type="button" className={`composer-option ${selected.length ? "selected" : ""}`} onClick={() => setSourcePicker(true)}><Database size={15}/>{selected.length ? `${selected.length} sources` : "Sources"}<ChevronDown size={12}/></button><button type="button" className={`composer-option ${webEnabled ? "selected" : ""}`} aria-pressed={webEnabled} aria-label="Automatic web search" onClick={() => void toggleWeb()}><Globe size={15}/> Web {webEnabled ? "on" : "off"}</button></div>{busy ? <button type="button" className="send-button" aria-label="Stop generation" onClick={() => { controller.current?.abort(); setBusy(false); }}><Square size={16}/></button> : <button className="send-button" aria-label="Send message" disabled={!question.trim()}><ArrowUp size={20}/></button>}</div></form>
          <input ref={attachment} type="file" className="sr-only" aria-label="Attach document" multiple accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.json" onChange={e => { if (e.target.files) void upload(e.target.files); e.target.value = ""; }}/>
          {busy && <p className="generation-status" role="status"><span className="pulse-dot"/>{stage}…</p>}
          {!messages.length && <div className="suggestions">{[{ icon: ChartNoAxesCombined, text: "Explore a business metric", query: "Which metrics are defined in the selected sources, and how are they calculated?" }, { icon: FileText, text: "Find the important details", query: "Summarize the key commitments in the selected documents, with citations." }, { icon: Database, text: "Investigate across sources", query: "What evidence is available to investigate customer renewal risk across these sources?" }].map(item => <button key={item.text} onClick={() => setQuestion(item.query)}><item.icon size={17}/><span>{item.text}</span><Plus size={13}/></button>)}</div>}
          <p className="composer-footnote">Grounded in your sources. Always review important findings.<span> · {webEnabled ? "Public web available when relevant" : "Web is off"}</span></p>
        </div>
      </div> : view === "documents" || view === "connections" ? <Sources key={view + me.workspace_id} sources={sources} documents={view === "documents"} isAdmin={me.role === "admin"} refresh={refresh} onAsk={id => newChat([id])} report={report}/> : view === "setup" ? <Setup navigate={setView} report={report}/> : view === "insights" ? <Insights key={me.workspace_id} report={report}/> : <Settings key={view + me.workspace_id} me={me} account={view === "account"} reload={loadMe} logout={logout} report={report}/>}
    </main>
    <AnimatePresence>{inspector && <motion.aside className="inspector" initial={{ opacity: 0, x: 15 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}><div className="section-heading"><h2>Behind the answer</h2><button className="icon-button" aria-label="Close inspector" onClick={() => setInspector(null)}><PanelRightClose size={19}/></button></div><p className="muted small">Evidence, queries and observed execution. Review the sources behind each finding.</p>
      <ExecutionSummary artifact={inspector.artifact}/><h3>Document evidence</h3>{inspector.artifact.evidence?.length ? inspector.artifact.evidence.map(item => <details key={item.chunk_id}><summary><FileText size={14}/>{item.filename}{item.page ? ` · p. ${item.page}` : ""}</summary><p className="muted small">{item.heading || item.section || "Document passage"} · version {item.version ?? "—"} · [D:{item.chunk_id}]</p><blockquote>{item.text}</blockquote><button onClick={() => void api<{ text: string }>(`/evidence/${item.chunk_id}`).then(data => { setInspector(current => current ? { ...current, artifact: { ...current.artifact, evidence: current.artifact.evidence?.map(e => e.chunk_id === item.chunk_id ? { ...e, text: data.text } : e) } } : null); }).catch(e => { setInspector(null); report(e.message); })}>Verify current access</button><small>{item.chunk_id}</small></details>) : <p className="muted small">No document passages used.</p>}
      {inspector.artifact.results?.map((result, i) => <details key={i} open><summary>Query result {i + 1} · {result.latency_ms} ms · [R:{i}]</summary><pre>{result.sql || JSON.stringify(result.mongo, null, 2)}</pre><p className="muted small">Source: {inspector.artifact.selected_sources?.find(s => s.id === result.source_id)?.name || result.source_id}</p><p className="muted small">{result.rows.length} returned rows{result.truncated ? " · capped" : ""}</p></details>)}
      {!!inspector.artifact.web?.length && <><h3>Public web</h3>{inspector.artifact.web.map(item => <div key={item.url}><a href={item.url} target="_blank" rel="noreferrer noopener">{item.title}</a><p className="muted small">{item.text}</p></div>)}</>}
      <h3>Execution timeline</h3><ol className="timeline">{inspector.artifact.trace?.map((item, i) => <li key={i}><span>{String(item.stage).replaceAll("_", " ")}</span>{item.latency_ms !== undefined && <small>{String(item.latency_ms)} ms</small>}<details><summary>Details</summary><pre>{JSON.stringify(item, null, 2)}</pre></details></li>)}</ol>{inspector.artifact.usage && <><h3>Model usage</h3><pre>{JSON.stringify(inspector.artifact.usage, null, 2)}</pre></>}
    </motion.aside>}</AnimatePresence>
    <Modal title="Choose your sources" open={sourcePicker} onClose={() => setSourcePicker(false)}><p className="muted small">New conversations include all ready sources you can access. Unselect any source to narrow this conversation. Web is controlled separately.</p><div className="row"><button onClick={() => { setScopeMode("all"); setSelected(sources.filter(s => s.status === "ready").map(s => s.id)); }}>Select all ready</button><button onClick={() => { setScopeMode("custom"); setSelected([]); }}>Clear all</button><span>{selected.length} selected</span></div>{sources.length ? sources.map(source => <label className="permission-row" key={source.id}><span>{source.name}<small>{source.kind} · {source.status}</small></span><input type="checkbox" aria-label={`Use ${source.name}`} disabled={source.status !== "ready"} checked={selected.includes(source.id)} onChange={e => { setScopeMode("custom"); setSelected(e.target.checked ? [...selected, source.id] : selected.filter(id => id !== source.id)); }}/></label>) : <p>Add a document or data connection to get started.</p>}<button className="primary full" onClick={() => void saveScope()}>Use selected sources</button></Modal>
    <Modal title="Save this insight" open={!!saving} onClose={() => setSaving(null)}><form onSubmit={saveWidget}><Field label="Dashboard"><select name="dashboard"><option value="">Create a new dashboard</option>{dashboards.map(d => <option value={d.id} key={d.id}>{d.name}</option>)}</select></Field><Field label="New dashboard name"><input name="name" defaultValue="My insights" maxLength={100}/></Field><button className="primary">Save insight</button></form></Modal>
    <Modal title="Delete this conversation?" open={!!deleting} onClose={() => setDeleting(null)}><p>This removes the conversation and its saved result widgets.</p><button className="danger" onClick={() => void api(`/conversations/${deleting?.id}`, "DELETE").then(async () => { if (deleting?.id === chatId) newChat(); setDeleting(null); await refreshChats(); }).catch(e => report(e.message))}>Delete conversation</button></Modal>
  </div>;
}
