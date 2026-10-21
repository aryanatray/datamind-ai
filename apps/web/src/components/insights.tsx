"use client";
import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Dashboard, Widget } from "@/lib/types";
import { Analytics } from "./analytics";
import { Empty, Field, Modal } from "./primitives";

export function Insights({ report }: { report: (s: string) => void }) {
  const [dashboards, setDashboards] = useState<Dashboard[]>([]); const [adding, setAdding] = useState(false); const [busy, setBusy] = useState(false);
  useEffect(() => { api<Dashboard[]>("/dashboards").then(setDashboards).catch(e => report(e.message)); }, [report]);
  async function load() { setDashboards(await api<Dashboard[]>("/dashboards")); }
  async function act(fn: () => Promise<unknown>) { setBusy(true); try { await fn(); await load(); } catch (e) { report((e as Error).message); } finally { setBusy(false); } }
  function clean(widget: Widget) { return { message_id: widget.message_id, result_index: widget.result_index, chart: widget.chart }; }
  function reorder(dashboard: Dashboard, index: number, direction: number) {
    const widgets = [...dashboard.widgets]; [widgets[index], widgets[index + direction]] = [widgets[index + direction], widgets[index]];
    void act(() => api(`/dashboards/${dashboard.id}`, "PUT", { name: dashboard.name, widgets: widgets.map(clean) }));
  }
  return <section className="page"><div className="page-title"><div><span className="eyebrow">FROM ANSWERS TO ACTION</span><h1>Insights studio</h1><p>Keep the findings that matter. Revisit the evidence anytime.</p></div><button className="primary" onClick={() => setAdding(true)}><Plus size={17}/> New dashboard</button></div>
    {dashboards.length === 0 && <Empty title="Your next insight belongs here" text="Save a chart or data table from a conversation, or create a dashboard to organize your findings."/>}
    {dashboards.map(dashboard => <section className="dashboard" key={dashboard.id}><div className="section-heading"><form className="row" onSubmit={e => { e.preventDefault(); const name = new FormData(e.currentTarget).get("name"); void act(() => api(`/dashboards/${dashboard.id}`, "PUT", { name, widgets: dashboard.widgets.map(clean) })); }}><input aria-label="Dashboard name" name="name" defaultValue={dashboard.name} required maxLength={100}/><button disabled={busy}>Rename</button></form><div className="row"><button disabled={busy} onClick={() => void act(() => api(`/dashboards/${dashboard.id}/refresh`, "POST"))}><RefreshCw size={16}/> Refresh</button><button className="icon-button danger" aria-label={`Delete ${dashboard.name}`} disabled={busy} onClick={() => void act(() => api(`/dashboards/${dashboard.id}`, "DELETE"))}><Trash2 size={16}/></button></div></div>
      <div className="widget-grid">{dashboard.widgets.length === 0 && <p className="muted">Save a result from a conversation to add your first widget.</p>}{dashboard.widgets.map((widget, index) => <article key={`${widget.message_id}-${index}`} className="widget"><div className="section-heading"><h3>{widget.restricted ? "Source access required" : widget.chart.title}</h3><div className="row"><button className="icon-button" aria-label="Move widget up" disabled={index === 0 || busy} onClick={() => reorder(dashboard, index, -1)}><ArrowUp size={14}/></button><button className="icon-button" aria-label="Move widget down" disabled={index === dashboard.widgets.length - 1 || busy} onClick={() => reorder(dashboard, index, 1)}><ArrowDown size={14}/></button><button className="icon-button" aria-label="Remove widget" disabled={busy} onClick={() => void act(() => api(`/dashboards/${dashboard.id}`, "PUT", { name: dashboard.name, widgets: dashboard.widgets.filter((_, i) => i !== index).map(clean) }))}><Trash2 size={14}/></button></div></div>{widget.result ? <Analytics result={widget.result} initial={widget.chart} exportUrl={`/api/messages/${widget.message_id}/export?result_index=${widget.result_index}`}/> : <p className="muted">This saved result is unavailable under your current permissions.</p>}</article>)}</div>
    </section>)}
    <Modal title="Create a dashboard" open={adding} onClose={() => setAdding(false)}><form onSubmit={e => { e.preventDefault(); const name = new FormData(e.currentTarget).get("name"); void act(async () => { await api("/dashboards", "POST", { name, widgets: [] }); setAdding(false); }); }}><Field label="Dashboard name"><input name="name" required maxLength={100} autoFocus/></Field><button className="primary" disabled={busy}>Create dashboard</button></form></Modal>
  </section>;
}
