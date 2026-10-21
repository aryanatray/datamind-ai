"use client";
import { useState } from "react";
import { ArrowUpRight, Network } from "lucide-react";
import { api } from "@/lib/api";
import { ErrorBox, Field } from "./primitives";

export function Auth({ onLogin }: { onLogin: () => Promise<void> }) {
  const [signup, setSignup] = useState(false); const [mode, setMode] = useState("personal");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const body = { email: form.get("email"), password: form.get("password"), ...(signup ? { name: form.get("name"), mode, workspace: form.get("workspace"), capacity: Number(form.get("capacity") || 1) } : {}) };
      await api(`/auth/${signup ? "signup" : "login"}`, "POST", body); await onLogin();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <main className="auth-page"><div className="auth-brand"><Network size={30}/><span>DataMind<span className="muted">.AI</span></span></div><section className="auth-card"><span className="eyebrow">YOUR PRIVATE DATA WORKSPACE</span><h1>{signup ? "Make room for insight." : "Welcome back."}</h1><p className="muted">Your data. One conversation. Infinite insights.</p><form onSubmit={submit}>
    {signup && <Field label="Your name"><input name="name" autoComplete="name" required maxLength={100}/></Field>}
    <Field label="Email"><input name="email" type="email" autoComplete="email" required/></Field>
    <Field label="Password"><input name="password" type="password" minLength={signup ? 12 : undefined} maxLength={128} autoComplete={signup ? "new-password" : "current-password"} required/></Field>
    {signup && <><Field label="Workspace type"><select value={mode} onChange={e => setMode(e.target.value)}><option value="personal">Personal · private to you</option><option value="enterprise">Enterprise · a shared team</option></select></Field><Field label="Workspace name"><input name="workspace" defaultValue="My workspace" required maxLength={100}/></Field>{mode === "enterprise" && <Field label="Member capacity"><input name="capacity" type="number" defaultValue={5} min={1} max={1000}/></Field>}</>}
    <ErrorBox error={error}/><button className="primary full" disabled={busy}>{busy ? "Please wait…" : signup ? "Create workspace" : "Sign in"}<ArrowUpRight size={17}/></button>
  </form><p className="auth-switch">{signup ? "Already have an account?" : "New to DataMind?"} <button onClick={() => { setSignup(!signup); setError(""); }}>{signup ? "Sign in" : "Create an account"}</button></p><small>Self-hosted. No subscriptions. Your workspace stays yours.</small></section></main>;
}
