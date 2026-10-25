import clsx from "clsx";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

export function formatWhen(value: string | null | undefined) {
  if (!value) return "—";
  const hasZone = /Z$|[+-]\d\d:?\d\d$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-ink text-white hover:bg-black",
        variant === "secondary" && "border border-line bg-card hover:bg-paper",
        variant === "danger" && "bg-red-800 text-white hover:bg-red-900",
        variant === "ghost" && "hover:bg-black/5",
        className,
      )}
      {...props}
    />
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx("w-full rounded-xl border border-line bg-white px-3 py-2 text-sm", props.className)} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx("w-full rounded-xl border border-line bg-white px-3 py-2 text-sm", props.className)} />;
}

export function SelectInput(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx("w-full rounded-xl border border-line bg-white px-3 py-2 text-sm", props.className)} />;
}

export function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="font-medium text-ink/80">{label}</span>
      {children}
      {error ? <span className="block text-xs text-red-700">{error}</span> : null}
    </label>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "hot" | "warm" | "cold" | "good" | "bad" | "info" }) {
  return (
    <span
      className={clsx(
        "inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize",
        tone === "neutral" && "bg-stone-200 text-stone-800",
        tone === "hot" && "bg-orange-100 text-orange-800",
        tone === "warm" && "bg-amber-100 text-amber-900",
        tone === "cold" && "bg-sky-100 text-sky-900",
        tone === "good" && "bg-emerald-100 text-emerald-900",
        tone === "bad" && "bg-red-100 text-red-800",
        tone === "info" && "bg-blue-100 text-blue-900",
      )}
    >
      {children}
    </span>
  );
}

export function priorityTone(priority: string): "hot" | "warm" | "cold" | "neutral" {
  if (priority === "hot" || priority === "warm" || priority === "cold") return priority;
  return "neutral";
}

export function statusTone(status: string): "good" | "bad" | "info" | "warm" | "neutral" {
  if (["succeeded", "processed", "provider_accepted", "connected"].includes(status)) return "good";
  if (["failed", "send_failed", "needs_review"].includes(status)) return "bad";
  if (["running", "queued", "retrying", "send_attempt", "pending_approval", "approved"].includes(status)) return "info";
  if (status === "send_unknown" || status === "incomplete") return "warm";
  return "neutral";
}

export function Banner({ children }: { children: ReactNode }) {
  return <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">{children}</div>;
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={clsx("rounded-3xl border border-line bg-card p-5 shadow-card", className)}>{children}</section>;
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-3xl border border-dashed border-line px-6 py-10 text-center">
      <h3 className="font-display text-xl">{title}</h3>
      <p className="mt-2 text-sm text-ink/60">{body}</p>
    </div>
  );
}

export const STATUSES = ["new", "contacted", "qualified", "quotation", "negotiation", "won", "lost", "archived"];
export const PRIORITIES = ["unset", "hot", "warm", "cold"];
