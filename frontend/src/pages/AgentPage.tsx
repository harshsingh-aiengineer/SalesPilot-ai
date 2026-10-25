import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorText } from "../lib/api";
import type { AgentRun, ChatMessage, Conversation } from "../lib/types";
import { Badge, Banner, Button, Card, TextArea, formatWhen, statusTone } from "../components/ui";

export function AgentPage() {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [runId, setRunId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => api<Conversation[]>("/conversations") });
  const thread = useQuery({
    queryKey: ["conversation", activeId],
    enabled: activeId !== null,
    queryFn: () => api<{ messages: ChatMessage[]; runs: AgentRun[] }>(`/conversations/${activeId}`),
  });
  const run = useQuery({
    queryKey: ["run", runId],
    enabled: runId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
    queryFn: () => api<AgentRun>(`/runs/${runId}`),
  });

  useEffect(() => {
    if (activeId === null && conversations.data && conversations.data.length > 0) {
      setActiveId(conversations.data[0].id);
    }
  }, [activeId, conversations.data]);

  useEffect(() => {
    if (run.data && !["queued", "running"].includes(run.data.status)) {
      queryClient.invalidateQueries({ queryKey: ["conversation", activeId] });
    }
  }, [run.data?.status]);

  const send = useMutation({
    mutationFn: async () => {
      let conversationId = activeId;
      if (conversationId === null) {
        const conversation = await api<Conversation>("/conversations", { method: "POST", body: JSON.stringify({ title: "New chat" }) });
        conversationId = conversation.id;
        setActiveId(conversation.id);
      }
      return api<{ run: AgentRun }>(`/conversations/${conversationId}/messages`, { method: "POST", body: JSON.stringify({ content: draft }) });
    },
    onSuccess: async (result) => {
      setDraft("");
      setError("");
      setRunId(result.run.id);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["conversation", result.run.conversation_id] });
    },
    onError: (err) => setError(errorText(err)),
  });

  const activeRun = run.data?.conversation_id === activeId ? run.data : thread.data?.runs?.[0];

  return (
    <div className="space-y-4">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-brass">Agent</p>
        <h1 className="font-display text-4xl">Workspace</h1>
      </header>
      {error ? <Banner>{error}</Banner> : null}
      <div className="grid gap-4 lg:grid-cols-[240px_1fr_320px]">
        <Card className="space-y-2">
          <p className="text-xs uppercase tracking-[0.16em] text-ink/50">Saved chats</p>
          {conversations.isLoading ? <p>Loading…</p> : null}
          {conversations.data?.length === 0 ? <p className="text-sm text-ink/60">No saved chats yet. Start a chat, then send a message.</p> : null}
          {conversations.data?.map((conversation) => (
            <button key={conversation.id} className={`w-full rounded-2xl px-3 py-2 text-left text-sm ${activeId === conversation.id ? "bg-ink text-white" : "hover:bg-paper"}`} onClick={() => { setActiveId(conversation.id); setRunId(null); setError(""); }}>
              {conversation.title}
            </button>
          ))}
        </Card>
        <Card className="flex min-h-[520px] flex-col">
          <div className="flex-1 space-y-3">
            {thread.data?.messages.map((message) => (
              <div key={message.id} className={`max-w-[90%] rounded-2xl px-4 py-3 text-sm ${message.role === "user" ? "ml-auto bg-ink text-white" : "bg-paper"}`}>
                <div className="mb-1 text-[10px] uppercase tracking-wide opacity-70">{message.role}</div>
                <p className="whitespace-pre-wrap">{message.content}</p>
              </div>
            ))}
            {activeRun && ["queued", "running"].includes(activeRun.status) ? <p className="text-sm text-ink/60" aria-busy="true">Agent is {activeRun.status}…</p> : null}
            {activeRun?.error_message && activeRun.status === "failed" ? <Banner>This saved run failed: {activeRun.error_message}</Banner> : null}
          </div>
          <div className="mt-4 space-y-2">
            <TextArea rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask about leads, emails, or follow-ups" aria-label="Message" />
            <div className="flex gap-2">
              <Button disabled={!draft.trim() || send.isPending} onClick={() => send.mutate()}>{send.isPending ? "Sending…" : "Send"}</Button>
              {activeRun && ["queued", "running"].includes(activeRun.status) ? (
                <Button variant="secondary" onClick={async () => {
                  await api(`/runs/${activeRun.id}/cancel`, { method: "POST" });
                  await queryClient.invalidateQueries({ queryKey: ["run", activeRun.id] });
                }}>Cancel</Button>
              ) : null}
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center justify-between">
            <h2 className="font-display text-2xl">Activity</h2>
            {activeRun ? <Badge tone={statusTone(activeRun.status)}>{activeRun.status}</Badge> : null}
          </div>
          {!activeRun ? <p className="mt-3 text-sm text-ink/60">Tool calls appear here after you send a message.</p> : null}
          <div className="mt-3 space-y-3">
            {activeRun?.steps.map((step) => (
              <details key={step.id} className="rounded-2xl border border-line p-3 text-sm" open>
                <summary className="cursor-pointer font-medium">{step.sequence_number}. {step.tool_name} · {step.status}</summary>
                <p className="mt-2 text-xs text-ink/50">{formatWhen(step.started_at)}</p>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs">Input: {step.input_summary}</pre>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs">Result: {step.result_summary}</pre>
              </details>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
