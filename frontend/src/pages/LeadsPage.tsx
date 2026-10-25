import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { api, errorText } from "../lib/api";
import { leadFormSchema, toLeadPayload, type LeadFormValues } from "../lib/schemas";
import type { EmailItem, Lead } from "../lib/types";
import { Badge, Banner, Button, Card, Empty, Field, PRIORITIES, STATUSES, SelectInput, TextArea, TextInput, formatWhen, priorityTone } from "../components/ui";

const blank: LeadFormValues = {
  name: "",
  email: "",
  phone: "",
  company: "",
  location: "",
  requirements: "",
  budget_min: "",
  budget_max: "",
  currency: "AED",
  source: "",
  status: "new",
  priority: "unset",
  next_action: "",
  next_follow_up_at: "",
  notes: "",
};

function fromLead(lead: Lead): LeadFormValues {
  return {
    ...blank,
    name: lead.name,
    email: lead.email ?? "",
    phone: lead.phone ?? "",
    company: lead.company ?? "",
    location: lead.location ?? "",
    requirements: lead.requirements ?? "",
    budget_min: lead.budget_min?.toString() ?? "",
    budget_max: lead.budget_max?.toString() ?? "",
    currency: lead.currency || "AED",
    source: lead.source ?? "",
    status: lead.status as LeadFormValues["status"],
    priority: lead.priority as LeadFormValues["priority"],
    next_action: lead.next_action ?? "",
    next_follow_up_at: lead.next_follow_up_at ? lead.next_follow_up_at.slice(0, 16) : "",
    notes: lead.notes ?? "",
  };
}

export function LeadsPage() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<Lead | "new" | null>(null);
  const [note, setNote] = useState("");
  const [actionError, setActionError] = useState("");

  const leads = useQuery({
    queryKey: ["leads", q, status, priority],
    queryFn: () => api<{ items: Lead[]; total: number }>(`/leads?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}${status ? `&status=${status}` : ""}${priority ? `&priority=${priority}` : ""}`),
  });
  const detail = useQuery({
    queryKey: ["lead-detail", typeof editing === "object" && editing ? editing.id : null],
    enabled: typeof editing === "object" && editing !== null,
    queryFn: async () => {
      const id = (editing as Lead).id;
      const [notes, activity, emails] = await Promise.all([
        api<Array<{ id: number; body: string; created_at: string; author_type: string }>>(`/leads/${id}/notes`),
        api<Array<{ id: number; summary: string; activity_type: string; created_at: string }>>(`/leads/${id}/activity`),
        api<{ items: EmailItem[] }>(`/leads/${id}/emails`),
      ]);
      return { notes, activity, emails: emails.items };
    },
  });

  const save = useMutation({
    mutationFn: async (values: LeadFormValues) => {
      const payload = toLeadPayload(values);
      if (editing && editing !== "new") return api<Lead>(`/leads/${editing.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      return api<Lead>("/leads", { method: "POST", body: JSON.stringify(payload) });
    },
    onSuccess: async () => {
      setEditing(null);
      setActionError("");
      await queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api(`/leads/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      setEditing(null);
      await queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const bulk = useMutation({
    mutationFn: (body: object) => api("/leads/bulk", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: async () => {
      setSelected([]);
      await queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const addNote = useMutation({
    mutationFn: () => api(`/leads/${(editing as Lead).id}/notes`, { method: "POST", body: JSON.stringify({ body: note }) }),
    onSuccess: async () => {
      setNote("");
      await queryClient.invalidateQueries({ queryKey: ["lead-detail"] });
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const form = useForm<LeadFormValues>({ resolver: zodResolver(leadFormSchema), defaultValues: blank });

  function openEditor(lead: Lead | "new") {
    setActionError("");
    setEditing(lead);
    form.reset(lead === "new" ? blank : fromLead(lead));
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-brass">CRM</p>
          <h1 className="font-display text-4xl">Leads</h1>
        </div>
        <Button onClick={() => openEditor("new")}>New lead</Button>
      </header>
      {actionError ? <Banner>{actionError}</Banner> : null}
      <Card>
        <div className="grid gap-3 md:grid-cols-4">
          <TextInput placeholder="Search name, email, location" value={q} onChange={(event) => setQ(event.target.value)} aria-label="Search leads" />
          <SelectInput value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter status">
            <option value="">All statuses</option>
            {STATUSES.map((item) => <option key={item}>{item}</option>)}
          </SelectInput>
          <SelectInput value={priority} onChange={(event) => setPriority(event.target.value)} aria-label="Filter priority">
            <option value="">All priorities</option>
            {PRIORITIES.map((item) => <option key={item}>{item}</option>)}
          </SelectInput>
          <Button variant="secondary" onClick={() => leads.refetch()}>Refresh</Button>
        </div>
      </Card>
      {leads.isLoading ? <p aria-busy="true">Loading leads…</p> : null}
      {leads.error ? <Banner>{errorText(leads.error)}</Banner> : null}
      {leads.data && leads.data.items.length === 0 ? <Empty title="No leads match" body="Create a lead or change the filters." /> : null}
      {leads.data && leads.data.items.length > 0 ? (
        <div className="overflow-x-auto rounded-3xl border border-line bg-card">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-ink/50">
              <tr>
                <th className="px-4 py-3"></th>
                <th className="px-4 py-3">Lead</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Priority</th>
                <th className="px-4 py-3">Follow-up</th>
                <th className="px-4 py-3">Score</th>
              </tr>
            </thead>
            <tbody>
              {leads.data.items.map((lead) => (
                <tr key={lead.id} className="border-t border-line">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      aria-label={`Select ${lead.name}`}
                      checked={selected.includes(lead.id)}
                      onChange={(event) => setSelected((current) => event.target.checked ? [...current, lead.id] : current.filter((id) => id !== lead.id))}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <button className="text-left" onClick={() => openEditor(lead)}>
                      <div className="font-medium">{lead.name}</div>
                      <div className="text-xs text-ink/50">{lead.email || "No email"} · {lead.location || "No location"}{lead.is_synthetic ? " · synthetic" : ""}</div>
                    </button>
                  </td>
                  <td className="px-4 py-3"><Badge>{lead.status}</Badge></td>
                  <td className="px-4 py-3"><Badge tone={priorityTone(lead.priority)}>{lead.priority}</Badge></td>
                  <td className="px-4 py-3">{formatWhen(lead.next_follow_up_at)}</td>
                  <td className="px-4 py-3">{lead.qualification_score ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {selected.length > 0 ? (
        <Card className="flex flex-wrap items-center gap-3">
          <span className="text-sm">{selected.length} selected</span>
          <Button variant="secondary" onClick={() => bulk.mutate({ action: "set_status", ids: selected, status: "contacted" })}>Mark contacted</Button>
          <Button
            variant="danger"
            onClick={() => {
              if (window.confirm(`Delete ${selected.length} leads? This cannot be undone.`)) bulk.mutate({ action: "delete", ids: selected, confirm: true });
            }}
          >
            Delete selected
          </Button>
        </Card>
      ) : null}
      {editing ? (
        <div className="fixed inset-0 z-20 flex justify-end bg-black/30" onClick={() => setEditing(null)}>
          <div className="h-full w-full max-w-xl overflow-y-auto bg-paper p-6" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between">
              <h2 className="font-display text-3xl">{editing === "new" ? "New lead" : editing.name}</h2>
              <Button variant="ghost" onClick={() => setEditing(null)}>Close</Button>
            </div>
            <form className="mt-4 space-y-3" onSubmit={form.handleSubmit((values) => save.mutate(values))}>
              <Field label="Name" error={form.formState.errors.name?.message}><TextInput {...form.register("name")} /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Email" error={form.formState.errors.email?.message}><TextInput {...form.register("email")} /></Field>
                <Field label="Phone"><TextInput {...form.register("phone")} /></Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Company"><TextInput {...form.register("company")} /></Field>
                <Field label="Location"><TextInput {...form.register("location")} /></Field>
              </div>
              <Field label="Requirements"><TextArea rows={3} {...form.register("requirements")} /></Field>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Budget min"><TextInput {...form.register("budget_min")} /></Field>
                <Field label="Budget max"><TextInput {...form.register("budget_max")} /></Field>
                <Field label="Currency"><TextInput {...form.register("currency")} /></Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Status">
                  <SelectInput {...form.register("status")}>{STATUSES.map((item) => <option key={item}>{item}</option>)}</SelectInput>
                </Field>
                <Field label="Priority">
                  <SelectInput {...form.register("priority")}>{PRIORITIES.map((item) => <option key={item}>{item}</option>)}</SelectInput>
                </Field>
              </div>
              <Field label="Source"><TextInput {...form.register("source")} /></Field>
              <Field label="Next action"><TextInput {...form.register("next_action")} /></Field>
              <Field label="Follow-up"><TextInput type="datetime-local" {...form.register("next_follow_up_at")} /></Field>
              <Field label="Notes"><TextArea rows={3} {...form.register("notes")} /></Field>
              <div className="flex gap-2">
                <Button disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
                {editing !== "new" ? <Button type="button" variant="danger" onClick={() => { if (window.confirm("Delete this lead?")) remove.mutate(editing.id); }}>Delete</Button> : null}
              </div>
            </form>
            {editing !== "new" ? (
              <div className="mt-8 space-y-4">
                <h3 className="font-display text-2xl">Qualification</h3>
                <p className="text-sm text-ink/70">{editing.qualification_summary || "Not qualified yet."}</p>
                <p className="text-xs text-ink/50">Method: {editing.qualification_method || "—"} · Confidence: {editing.qualification_confidence ?? "—"} · Missing: {editing.missing_information || "—"}</p>
                <Button variant="secondary" onClick={async () => {
                  const id = editing.id;
                  setActionError("");
                  try {
                    const result = await api<{ llm_error?: string }>(`/leads/${id}/qualify`, { method: "POST" });
                    const fresh = await api<Lead>(`/leads/${id}`);
                    if (result.llm_error) setActionError(`Rules were saved. The local model was not used: ${result.llm_error}`);
                    setEditing(fresh);
                    form.reset(fromLead(fresh));
                    await queryClient.invalidateQueries({ queryKey: ["leads"] });
                  } catch (error) {
                    setActionError(errorText(error));
                  }
                }}>Qualify</Button>
                <h3 className="font-display text-2xl">Timeline</h3>
                {detail.isLoading ? <p>Loading activity…</p> : null}
                {detail.data?.activity.map((item) => (
                  <div key={item.id} className="border-b border-line py-2 text-sm">
                    <div className="font-medium">{item.summary}</div>
                    <div className="text-xs text-ink/50">{item.activity_type} · {formatWhen(item.created_at)}</div>
                  </div>
                ))}
                <h3 className="font-display text-2xl">Notes</h3>
                {detail.data?.notes.map((item) => <p key={item.id} className="text-sm">{item.body}</p>)}
                <div className="flex gap-2">
                  <TextInput value={note} onChange={(event) => setNote(event.target.value)} placeholder="Add a note" aria-label="Note" />
                  <Button type="button" variant="secondary" disabled={!note.trim() || addNote.isPending} onClick={() => addNote.mutate()}>Add</Button>
                </div>
                <h3 className="font-display text-2xl">Linked emails</h3>
                {detail.data?.emails.length === 0 ? <p className="text-sm text-ink/60">No linked emails.</p> : null}
                {detail.data?.emails.map((email) => (
                  <div key={email.id} className="text-sm">
                    <div className="font-medium">{email.subject || "(no subject)"}</div>
                    <div className="text-xs text-ink/50">{email.sender} · {email.processing_status}{email.is_synthetic ? " · synthetic" : ""}</div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
