"use client";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

export function Modal({ title, children, open, onClose }: { title: string; children: ReactNode; open: boolean; onClose: () => void }) {
  return <Dialog.Root open={open} onOpenChange={value => { if (!value) onClose(); }}><Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="modal" aria-describedby={undefined}><div className="section-heading"><Dialog.Title>{title}</Dialog.Title><Dialog.Close className="icon-button" aria-label="Close dialog"><X size={18}/></Dialog.Close></div>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{isValidElement(children) ? cloneElement(children as ReactElement<{id?: string}>, {id}) : children}</div>;
}
export function ErrorBox({ error }: { error: string }) { return error ? <div role="alert" className="error">{error}</div> : null; }
export function Empty({ title, text }: { title: string; text: string }) { return <div className="empty"><h3>{title}</h3><p>{text}</p></div>; }
