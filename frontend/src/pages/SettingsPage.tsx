import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, errorText } from "../lib/api";
import { Badge, Banner, Button, Card, statusTone } from "../components/ui";

type Status = {
  ollama: { reachable: boolean; available: boolean; model: string; error: string | null; installed_models?: string[] };
  gmail: { connected: boolean; email: string | null; reason: string | null; message: string; secrets_configured: boolean };
  worker: { running: boolean; status: string; last_seen_at: string | null; message: string | null };
  inbox_query: string;
  business_timezone: string;
  dev_mode: boolean;
  insecure_default_password: boolean;
  max_concurrent_llm: number;
  test_recipient_configured: boolean;
  model: string;
  client_secrets_path?: string;
};

export function SettingsPage() {
  const [params] = useSearchParams();
  const [actionError, setActionError] = useState("");
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<Status>("/settings/status"), refetchInterval: 15000 });
  const probe = useQuery({ queryKey: ["model-probe"], queryFn: () => api<{ ok: boolean; reply: string | null; error: string | null }>("/settings/model-check", { method: "POST" }), enabled: false });

  if (status.isLoading) return <p aria-busy="true">Checking connections…</p>;
  if (status.error) return <Banner>{errorText(status.error)}</Banner>;
  const data = status.data!;
  const gmailNote = params.get("gmail");

  return (
    <div className="space-y-5">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-brass">Integrations</p>
        <h1 className="font-display text-4xl">Settings</h1>
      </header>
      {gmailNote === "connected" ? <Card>Gmail authorization returned. The profile check above is the source of truth.</Card> : null}
      {gmailNote === "error" ? <Banner>Gmail authorization did not complete ({params.get("reason") || "unknown"}).</Banner> : null}
      {actionError ? <Banner>{actionError}</Banner> : null}
      {data.insecure_default_password ? <Banner>The application password is still the default. Change SALESPILOT_PASSWORD before daily use.</Banner> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-2xl">Ollama</h2>
            <Badge tone={data.ollama.available ? "good" : "bad"}>{data.ollama.available ? "Model ready" : "Not ready"}</Badge>
          </div>
          <p className="text-sm">Configured model: {data.model}</p>
          <p className="text-sm">Reachable: {data.ollama.reachable ? "yes" : "no"}</p>
          <p className="text-sm">Concurrent model requests: {data.max_concurrent_llm}</p>
          {data.ollama.error ? <p className="text-sm text-red-700">{data.ollama.error}</p> : null}
          {data.ollama.installed_models ? <p className="text-xs text-ink/60">Installed: {data.ollama.installed_models.join(", ") || "none"}</p> : null}
          <Button variant="secondary" onClick={() => probe.refetch()} disabled={probe.isFetching}>{probe.isFetching ? "Probing…" : "Run readiness prompt"}</Button>
          {probe.data ? <p className="text-sm">{probe.data.ok ? `Model replied: ${probe.data.reply}` : probe.data.error}</p> : null}
          {probe.error ? <Banner>{errorText(probe.error)}</Banner> : null}
          <p className="text-xs text-ink/60">SalesPilot uses the installed model qwen2.5:3b.</p>
        </Card>
        <Card className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-2xl">Gmail</h2>
            <Badge tone={data.gmail.connected ? "good" : statusTone(data.gmail.reason || "failed")}>{data.gmail.connected ? "Connected" : "Not connected"}</Badge>
          </div>
          <p className="text-sm">{data.gmail.message}</p>
          <p className="text-sm">Account: {data.gmail.email || "—"}</p>
          <p className="text-sm">Client secret file: {data.gmail.secrets_configured ? "present" : "missing"}</p>
          <p className="text-sm">Inbox query: {data.inbox_query}</p>
          <p className="text-sm">Test recipient configured: {data.test_recipient_configured ? "yes" : "no"}</p>
          <Button onClick={async () => {
            setActionError("");
            try {
              const result = await api<{ authorization_url: string }>("/gmail/authorize", { method: "POST" });
              window.location.assign(result.authorization_url);
            } catch (err) {
              setActionError(errorText(err));
            }
          }}>Authorize Gmail</Button>
          <Button variant="secondary" onClick={async () => {
            setActionError("");
            try {
              await api("/gmail/disconnect", { method: "POST" });
              await status.refetch();
            } catch (err) {
              setActionError(errorText(err));
            }
          }}>Disconnect</Button>
          {!data.gmail.secrets_configured ? (
            <p className="text-sm text-ink/70">
              Save the downloaded OAuth client JSON as <span className="font-medium">{data.client_secrets_path || "data/secrets/google_client_secret.json"}</span>, then authorize. Connected appears only after a live Gmail profile request.
            </p>
          ) : null}
        </Card>
        <Card className="space-y-2">
          <h2 className="font-display text-2xl">Worker</h2>
          <Badge tone={data.worker.running ? "good" : "bad"}>{data.worker.running ? "Running" : data.worker.status}</Badge>
          <p className="text-sm">{data.worker.message}</p>
          <p className="text-xs text-ink/50">Last heartbeat: {data.worker.last_seen_at || "never"}</p>
        </Card>
        <Card className="space-y-2">
          <h2 className="font-display text-2xl">Local deployment</h2>
          <p className="text-sm">Business timezone: {data.business_timezone}</p>
          <p className="text-sm">Development mode: {data.dev_mode ? "on" : "off"}</p>
          <p className="text-sm text-ink/70">Services bind to 127.0.0.1. Do not publish this stack to the public internet. SQLite is intended for one machine and one worker.</p>
        </Card>
      </div>
    </div>
  );
}
