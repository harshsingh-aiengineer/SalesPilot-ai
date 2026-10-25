import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorText } from "../lib/api";
import type { AutomationSettings, Task } from "../lib/types";
import { Badge, Banner, Button, Card, Field, TextInput, formatWhen, statusTone } from "../components/ui";

export function AutomationPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<AutomationSettings | null>(null);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [logTask, setLogTask] = useState<number | null>(null);

  const settings = useQuery({ queryKey: ["automation-settings"], queryFn: () => api<AutomationSettings>("/automation/settings") });
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<Array<{ id: number; name: string; enabled: boolean; requires_approval: boolean }>>("/automation/rules") });
  const tasks = useQuery({
    queryKey: ["tasks", statusFilter],
    queryFn: () => api<{ items: Task[]; total: number }>(`/tasks?limit=50${statusFilter ? `&status=${statusFilter}` : ""}`),
  });
  const logs = useQuery({
    queryKey: ["task-logs", logTask],
    enabled: logTask !== null,
    queryFn: () => api<Array<{ id: number; level: string; message: string; created_at: string }>>(`/tasks/${logTask}/logs`),
  });

  useEffect(() => {
    if (settings.data) setForm(settings.data);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () => api<AutomationSettings>("/automation/settings", { method: "PUT", body: JSON.stringify(form) }),
    onSuccess: async () => {
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["automation-settings"] });
      await queryClient.invalidateQueries({ queryKey: ["rules"] });
    },
    onError: (err) => setError(errorText(err)),
  });

  function set<K extends keyof AutomationSettings>(key: K, value: AutomationSettings[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current);
  }

  if (settings.isLoading || !form) return <p aria-busy="true">Loading automation settings…</p>;
  if (settings.error) return <Banner>{errorText(settings.error)}</Banner>;

  return (
    <div className="space-y-5">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-brass">Worker</p>
        <h1 className="font-display text-4xl">Automation center</h1>
      </header>
      {error ? <Banner>{error}</Banner> : null}
      <Card className="grid gap-4 md:grid-cols-2">
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.inbox_poll_enabled} onChange={(event) => set("inbox_poll_enabled", event.target.checked)} /> Poll inbox</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.follow_up_scan_enabled} onChange={(event) => set("follow_up_scan_enabled", event.target.checked)} /> Scan due follow-ups</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.lead_creation_enabled} onChange={(event) => set("lead_creation_enabled", event.target.checked)} /> Create CRM leads</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.draft_generation_enabled} onChange={(event) => set("draft_generation_enabled", event.target.checked)} /> Generate drafts</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.send_requires_approval} onChange={(event) => set("send_requires_approval", event.target.checked)} /> Sending requires approval</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.auto_send_enabled} onChange={(event) => set("auto_send_enabled", event.target.checked)} /> Allow automatic sending</label>
        <Field label="Poll interval (seconds, 5–86400)"><TextInput type="number" min={5} value={form.inbox_poll_interval_seconds} onChange={(event) => set("inbox_poll_interval_seconds", Number(event.target.value))} /></Field>
        <Field label="Confidence threshold"><TextInput type="number" step="0.05" value={form.qualification_confidence_threshold} onChange={(event) => set("qualification_confidence_threshold", Number(event.target.value))} /></Field>
        <Field label="Gmail search query"><TextInput value={form.gmail_search_query} onChange={(event) => set("gmail_search_query", event.target.value)} /></Field>
        <Field label="Allowed recipients or domains"><TextInput value={form.allowed_recipient_scope} onChange={(event) => set("allowed_recipient_scope", event.target.value)} placeholder="* for anyone, or buyer@example.com, example.com" /></Field>
        <Field label="Max emails / hour"><TextInput type="number" value={form.max_emails_per_hour} onChange={(event) => set("max_emails_per_hour", Number(event.target.value))} /></Field>
        <Field label="Max emails / day"><TextInput type="number" value={form.max_emails_per_day} onChange={(event) => set("max_emails_per_day", Number(event.target.value))} /></Field>
        <Field label="Follow-up cooldown hours"><TextInput type="number" value={form.follow_up_cooldown_hours} onChange={(event) => set("follow_up_cooldown_hours", Number(event.target.value))} /></Field>
        <Field label="Max task attempts"><TextInput type="number" value={form.max_task_attempts} onChange={(event) => set("max_task_attempts", Number(event.target.value))} /></Field>
        <Field label="Model timeout seconds"><TextInput type="number" value={form.llm_request_timeout_seconds} onChange={(event) => set("llm_request_timeout_seconds", Number(event.target.value))} /></Field>
        <Field label="Business days"><TextInput value={form.business_days} onChange={(event) => set("business_days", event.target.value)} /></Field>
        <Field label="Hours start"><TextInput type="number" value={form.business_hours_start} onChange={(event) => set("business_hours_start", Number(event.target.value))} /></Field>
        <Field label="Hours end"><TextInput type="number" value={form.business_hours_end} onChange={(event) => set("business_hours_end", Number(event.target.value))} /></Field>
        <div className="md:col-span-2"><Button onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save configuration"}</Button></div>
      </Card>
      <Card>
        <h2 className="font-display text-2xl">Effective rules</h2>
        <ul className="mt-3 space-y-2 text-sm">
          {rules.data?.map((rule) => (
            <li key={rule.id} className="flex items-center justify-between border-b border-line py-2">
              <span>{rule.name}</span>
              <span className="flex gap-2">{rule.enabled ? <Badge tone="good">enabled</Badge> : <Badge tone="bad">disabled</Badge>}{rule.requires_approval ? <Badge tone="info">approval</Badge> : null}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-2xl">Tasks</h2>
          <select className="rounded-xl border border-line bg-white px-3 py-2 text-sm" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Task status">
            <option value="">All</option>
            {["queued", "running", "retrying", "succeeded", "failed", "cancelled"].map((item) => <option key={item}>{item}</option>)}
          </select>
        </div>
        {tasks.isLoading ? <p aria-busy="true">Loading tasks…</p> : null}
        {tasks.error ? <Banner>{errorText(tasks.error)}</Banner> : null}
        <div className="mt-3 space-y-2">
          {tasks.data?.items.map((task) => (
            <div key={task.id} className="rounded-2xl border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">#{task.id} {task.task_type}</span>
                <Badge tone={statusTone(task.status)}>{task.status}</Badge>
              </div>
              <p className="mt-1 text-xs text-ink/50">Attempts {task.attempts}/{task.max_attempts} · scheduled {formatWhen(task.scheduled_at)}</p>
              {task.last_error ? <p className="mt-2 text-red-700">{task.last_error}</p> : null}
              <div className="mt-2 flex gap-2">
                <Button variant="ghost" onClick={() => setLogTask(task.id)}>Logs</Button>
                {task.status === "failed" || task.status === "cancelled" ? (
                  <Button variant="secondary" onClick={async () => {
                    setError("");
                    try {
                      await api(`/tasks/${task.id}/retry`, { method: "POST" });
                      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
                    } catch (err) {
                      setError(errorText(err));
                    }
                  }}>Retry</Button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
        {logTask ? (
          <div className="mt-4 rounded-2xl bg-paper p-3 text-xs">
            <div className="mb-2 font-medium">Logs for task {logTask}</div>
            {logs.data?.map((line) => <p key={line.id}>{formatWhen(line.created_at)} [{line.level}] {line.message}</p>)}
            {logs.data?.length === 0 ? <p>No log lines.</p> : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}
