import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorText } from "../lib/api";
import type { EmailItem } from "../lib/types";
import { Badge, Banner, Button, Card, Empty, TextInput, formatWhen, statusTone } from "../components/ui";

export function InboxPage() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [linkId, setLinkId] = useState("");

  const list = useQuery({
    queryKey: ["emails", q, status],
    queryFn: () => api<{ items: EmailItem[]; total: number }>(`/emails?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}${status ? `&status=${status}` : ""}`),
  });
  const detail = useQuery({
    queryKey: ["email", openId],
    enabled: openId !== null,
    queryFn: () => api<EmailItem>(`/emails/${openId}`),
  });
  const settings = useQuery({ queryKey: ["status"], queryFn: () => api<{ inbox_query: string; gmail: { connected: boolean; message: string; email: string | null } }>("/settings/status") });

  const sync = useMutation({
    mutationFn: () => api<Record<string, unknown>>("/gmail/sync", { method: "POST" }),
    onSuccess: async (result) => {
      setError("");
      setMessage(`Sync finished. Created ${String(result.created ?? 0)}, already stored ${String(result.already_stored ?? 0)}.`);
      await queryClient.invalidateQueries({ queryKey: ["emails"] });
    },
    onError: (err) => setError(errorText(err)),
  });

  const process = useMutation({
    mutationFn: (id: number) => api<Record<string, unknown>>(`/emails/${id}/process`, { method: "POST" }),
    onSuccess: async (result) => {
      setError("");
      const llm = (result.extraction as { llm_error?: string } | undefined)?.llm_error;
      setMessage(`Processing status: ${String(result.status)}. ${result.lead_id ? `Lead #${String(result.lead_id)} (${String(result.link_result)}).` : String(result.link_result || "")}${llm ? ` Model note: ${llm}` : ""}`);
      await queryClient.invalidateQueries({ queryKey: ["emails"] });
      await queryClient.invalidateQueries({ queryKey: ["email"] });
    },
    onError: (err) => setError(errorText(err)),
  });

  async function linkExisting() {
    if (!openId || !linkId) return;
    setError("");
    try {
      await api(`/emails/${openId}/link`, { method: "POST", body: JSON.stringify({ lead_id: Number(linkId) }) });
      setMessage(`Linked email ${openId} to lead ${linkId}.`);
      await queryClient.invalidateQueries({ queryKey: ["emails"] });
      await queryClient.invalidateQueries({ queryKey: ["email"] });
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-brass">Inbox</p>
          <h1 className="font-display text-4xl">Gmail messages</h1>
          <p className="mt-2 max-w-2xl text-sm text-ink/70">Monitoring query: {settings.data?.inbox_query || "Loading…"}</p>
        </div>
        <Button onClick={() => sync.mutate()} disabled={sync.isPending}>{sync.isPending ? "Syncing…" : "Sync inbox"}</Button>
      </header>
      {error ? <Banner>{error}</Banner> : null}
      {message ? <Card><p className="text-sm">{message}</p></Card> : null}
      <div className="grid gap-3 md:grid-cols-[1fr_220px]">
        <TextInput value={q} onChange={(event) => setQ(event.target.value)} placeholder="Search sender or subject" aria-label="Search emails" />
        <select className="rounded-xl border border-line bg-white px-3 py-2 text-sm" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Processing status">
          <option value="">All processing states</option>
          {["unprocessed", "processed", "ignored", "needs_review", "failed"].map((item) => <option key={item}>{item}</option>)}
        </select>
      </div>
      {list.isLoading ? <p aria-busy="true">Loading inbox…</p> : null}
      {list.error ? <Banner>{errorText(list.error)}</Banner> : null}
      {list.data && list.data.items.length === 0 ? <Empty title="No stored messages" body="Authorize Gmail in Settings, label a test thread salespilot-test, then sync." /> : null}
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-2">
          {list.data?.items.map((email) => (
            <button key={email.id} className="w-full rounded-2xl border border-line bg-card p-4 text-left" onClick={() => setOpenId(email.id)}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{email.subject || "(no subject)"}</span>
                <Badge tone={statusTone(email.processing_status)}>{email.processing_status}</Badge>
              </div>
              <p className="mt-1 truncate text-xs text-ink/60">{email.sender} · {formatWhen(email.received_at)}</p>
              <p className="mt-2 line-clamp-2 text-sm text-ink/80">{email.snippet}</p>
            </button>
          ))}
        </div>
        <Card>
          {!detail.data ? <p className="text-sm text-ink/60">Open a message to read it.</p> : null}
          {detail.isLoading ? <p aria-busy="true">Loading message…</p> : null}
          {detail.data ? (
            <div className="space-y-3">
              <h2 className="font-display text-2xl">{detail.data.subject || "(no subject)"}</h2>
              <p className="text-sm text-ink/70">From {detail.data.sender}<br />{formatWhen(detail.data.received_at)}</p>
              <Badge tone={statusTone(detail.data.processing_status)}>{detail.data.processing_status}</Badge>
              {detail.data.classification ? <Badge>{detail.data.classification}</Badge> : null}
              {detail.data.lead_id ? <p className="text-sm">Linked lead #{detail.data.lead_id}</p> : null}
              <pre className="whitespace-pre-wrap rounded-2xl bg-paper p-3 text-sm">{detail.data.body_text || "No body was stored."}</pre>
              {detail.data.extraction_json ? (
                <details>
                  <summary className="cursor-pointer text-sm font-medium">Extracted facts</summary>
                  <pre className="mt-2 overflow-x-auto text-xs">{detail.data.extraction_json}</pre>
                </details>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button disabled={process.isPending} onClick={() => process.mutate(detail.data!.id)}>{process.isPending ? "Processing…" : "Process with local AI"}</Button>
              </div>
              <div className="flex gap-2">
                <TextInput value={linkId} onChange={(event) => setLinkId(event.target.value)} placeholder="Existing lead id" aria-label="Lead id" />
                <Button variant="secondary" onClick={linkExisting}>Link</Button>
              </div>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
