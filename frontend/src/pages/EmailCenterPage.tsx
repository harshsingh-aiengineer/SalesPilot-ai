import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorText } from "../lib/api";
import type { Draft } from "../lib/types";
import { Badge, Banner, Button, Card, Empty, Field, TextArea, TextInput, formatWhen, statusTone } from "../components/ui";

const labels: Record<string, string> = {
  local_draft: "Local draft",
  gmail_draft: "Gmail draft",
  pending_approval: "Pending approval",
  approved: "Approved, not sent",
  send_attempt: "Send attempt",
  provider_accepted: "Provider accepted",
  send_failed: "Send failed",
  send_unknown: "Send result unknown",
};

export function EmailCenterPage() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Draft | null>(null);
  const [recipient, setRecipient] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [leadId, setLeadId] = useState("");
  const [preview, setPreview] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const drafts = useQuery({
    queryKey: ["drafts", q],
    queryFn: () => api<Draft[]>(`/drafts${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  });

  function open(draft: Draft) {
    setSelected(draft);
    setRecipient(draft.recipient);
    setSubject(draft.subject);
    setBody(draft.body);
    setLeadId(draft.lead_id?.toString() ?? "");
    setPreview(false);
    setConfirmOpen(false);
    setError("");
  }

  const save = useMutation({
    mutationFn: async () => {
      const payload = { recipient, subject, body, lead_id: leadId ? Number(leadId) : null };
      if (!selected) return api<Draft>("/drafts", { method: "POST", body: JSON.stringify(payload) });
      return api<Draft>(`/drafts/${selected.id}`, { method: "PATCH", body: JSON.stringify(payload) });
    },
    onSuccess: async (draft) => {
      setNotice(`Saved draft #${draft.id} as ${labels[draft.status] || draft.status}.`);
      setError("");
      open(draft);
      await queryClient.invalidateQueries({ queryKey: ["drafts"] });
    },
    onError: (err) => setError(errorText(err)),
  });

  async function act(path: string, payload?: object) {
    if (!selected) return;
    setError("");
    try {
      const result = await api<Draft & { provider_message_id?: string; status?: string; message?: string }>(`/drafts/${selected.id}/${path}`, {
        method: "POST",
        body: payload ? JSON.stringify(payload) : undefined,
      });
      if (result.provider_message_id) setNotice(`Gmail accepted the message. Provider id: ${result.provider_message_id}`);
      else if ("status" in result && result.status) setNotice(`Status: ${labels[String(result.status)] || String(result.status)}`);
      else setNotice("Request completed.");
      await queryClient.invalidateQueries({ queryKey: ["drafts"] });
      const fresh = drafts.data?.find((item) => item.id === selected.id);
      const updated = await api<Draft[]>(`/drafts`);
      const match = updated.find((item) => item.id === selected.id) || fresh;
      if (match) open(match);
      setConfirmOpen(false);
    } catch (err) {
      setError(errorText(err));
      const updated = await api<Draft[]>("/drafts").catch(() => []);
      const match = updated.find((item) => item.id === selected.id);
      if (match) open(match);
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-brass">Mail</p>
          <h1 className="font-display text-4xl">Email center</h1>
        </div>
        <Button onClick={() => { setSelected(null); setRecipient(""); setSubject(""); setBody(""); setLeadId(""); setPreview(false); }}>New local draft</Button>
      </header>
      {error ? <Banner>{error}</Banner> : null}
      {notice ? <Card><p className="text-sm">{notice}</p></Card> : null}
      <TextInput value={q} onChange={(event) => setQ(event.target.value)} placeholder="Search recipient, subject, or body" aria-label="Search drafts" />
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-2">
          {drafts.isLoading ? <p aria-busy="true">Loading drafts…</p> : null}
          {drafts.error ? <Banner>{errorText(drafts.error)}</Banner> : null}
          {drafts.data?.length === 0 ? <Empty title="No drafts" body="Create a local draft or let automation prepare one for approval." /> : null}
          {drafts.data?.map((draft) => (
            <button key={draft.id} className="w-full rounded-2xl border border-line bg-card p-3 text-left" onClick={() => open(draft)}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{draft.subject || "(no subject)"}</span>
                <Badge tone={statusTone(draft.status)}>{labels[draft.status] || draft.status}</Badge>
              </div>
              <p className="mt-1 truncate text-xs text-ink/60">{draft.recipient}</p>
            </button>
          ))}
        </div>
        <Card className="space-y-3">
          <Field label="Recipient"><TextInput value={recipient} onChange={(event) => setRecipient(event.target.value)} /></Field>
          <Field label="Lead id"><TextInput value={leadId} onChange={(event) => setLeadId(event.target.value)} /></Field>
          <Field label="Subject"><TextInput value={subject} onChange={(event) => setSubject(event.target.value)} /></Field>
          <Field label="Body"><TextArea rows={10} value={body} onChange={(event) => setBody(event.target.value)} /></Field>
          {selected ? (
            <div className="text-xs text-ink/60">
              State: {labels[selected.status] || selected.status}. Approval: {selected.approval_state}.
              {selected.provider_draft_id ? ` Gmail draft id: ${selected.provider_draft_id}.` : ""}
              {selected.provider_message_id ? ` Gmail message id: ${selected.provider_message_id}.` : " No provider message id yet."}
              {selected.sent_at ? ` Sent at ${formatWhen(selected.sent_at)}.` : ""}
              {selected.send_error ? ` Failure: ${selected.send_error}` : ""}
            </div>
          ) : <p className="text-xs text-ink/60">This starts as a local draft. It is not in Gmail until you create a Gmail draft or send it.</p>}
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => save.mutate()} disabled={save.isPending || !recipient}>{save.isPending ? "Saving…" : "Save"}</Button>
            <Button variant="secondary" type="button" onClick={() => setPreview((value) => !value)}>Preview</Button>
            {selected ? <Button variant="secondary" onClick={() => act("approve")}>Approve</Button> : null}
            {selected ? <Button variant="secondary" onClick={() => act("gmail-draft")}>Create Gmail draft</Button> : null}
            {selected ? <Button onClick={() => setConfirmOpen(true)}>Send</Button> : null}
            {selected && selected.status === "send_unknown" ? <Button variant="secondary" onClick={() => act("reconcile")}>Reconcile</Button> : null}
          </div>
          {preview ? (
            <div className="rounded-2xl bg-paper p-4 text-sm">
              <p>To: {recipient}</p>
              <p className="font-medium">{subject}</p>
              <pre className="mt-3 whitespace-pre-wrap">{body}</pre>
            </div>
          ) : null}
          {confirmOpen && selected ? (
            <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm">
              <p>Send this message from the authorized Gmail account to <strong>{recipient}</strong>? This calls the Gmail API.</p>
              <div className="mt-3 flex gap-2">
                <Button onClick={() => act("send", { confirm: true, recipient })}>Confirm send</Button>
                <Button variant="secondary" onClick={() => setConfirmOpen(false)}>Cancel</Button>
              </div>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
