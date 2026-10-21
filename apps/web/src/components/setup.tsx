"use client";
import { useEffect, useState } from "react";
import { Check, Circle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

type Status = { provider_configured: boolean; connections_ready: number; documents_ready: number; resources_selected: boolean; personalized: boolean };
export function Setup({ navigate, report }: { navigate: (view: "documents" | "connections" | "account" | "chat") => void; report: (s: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  async function load() { try { setStatus(await api<Status>("/setup")); } catch (e) { report((e as Error).message); } }
  useEffect(() => { let active = true; api<Status>("/setup").then(s => { if (active) setStatus(s); }).catch(e => report(e.message)); return () => { active = false; }; }, [report]);
  const steps = status ? [
    { done: status.connections_ready > 0, title: "Connect a database", text: "Add PostgreSQL or MongoDB with a read-only account, then Test & discover.", view: "connections" as const },
    { done: status.resources_selected, title: "Choose resources and define metrics", text: "Select allowed tables or collections. Explain revenue, dates and customer identifiers.", view: "connections" as const },
    { done: status.documents_ready > 0, title: "Prepare your document library", text: "Upload a small text PDF, Word or Markdown file. Wait for Ready and inspect extracted text.", view: "documents" as const },
    { done: status.personalized, title: "Make DataMind yours", text: "Optional: add your role and preferred response style.", view: "account" as const },
  ] : [];
  return <section className="page setup-page"><div className="page-title"><div><span className="eyebrow">A CLEAR START</span><h1>Your workspace, ready for insight.</h1><p>Connect, verify, then ask. Each step reflects your current workspace access.</p></div><button onClick={() => void load()}><RefreshCw size={16}/> Check status</button></div>
    <div className="setup-progress" role="status">{steps.filter(s => s.done).length} of {steps.length || 4} steps complete</div>
    {steps.map((step, i) => <article className={`setup-step ${step.done ? "complete" : ""}`} key={step.title}><span className="step-number">{step.done ? <Check size={20}/> : i + 1}</span><div><h2>{step.title}</h2><p>{step.text}</p></div><button onClick={() => navigate(step.view)}>{step.done ? "Review" : "Set up"}</button></article>)}
    <article className="settings-card"><h2>Verify before asking</h2><p className="muted">Run a read-only query in the source inspector and compare its result with known data. Open a document and check its extracted text and file history.</p><p>{status?.provider_configured ? <Check size={16}/> : <Circle size={16}/>} Gemini {status?.provider_configured ? "key configured" : "key missing"} · Live inference has not been verified by this checklist.</p><button className="primary" onClick={() => navigate("chat")}>Start a conversation</button></article>
  </section>;
}
