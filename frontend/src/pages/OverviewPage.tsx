import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errorText } from "../lib/api";
import type { AgentRun } from "../lib/types";
import { Badge, Banner, Card, formatWhen, statusTone } from "../components/ui";

type Overview = {
  leads_by_status: Record<string, number>;
  hot: number;
  warm: number;
  cold: number;
  follow_ups_due_today: number;
  overdue_follow_ups: number;
  email_stats: Record<string, number>;
  recent_runs: AgentRun[];
  pending_approvals: number;
  failed_tasks: number;
  total_leads: number;
};

export function OverviewPage() {
  const query = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/overview") });
  if (query.isLoading) return <p aria-busy="true">Loading overview…</p>;
  if (query.error) return <Banner>{errorText(query.error)}</Banner>;
  const data = query.data!;
  const chart = Object.entries(data.leads_by_status).map(([status, count]) => ({ status, count }));
  const metrics = [
    ["Leads", data.total_leads],
    ["Hot", data.hot],
    ["Warm", data.warm],
    ["Cold", data.cold],
    ["Due today", data.follow_ups_due_today],
    ["Overdue", data.overdue_follow_ups],
    ["Approvals", data.pending_approvals],
    ["Failed tasks", data.failed_tasks],
  ];
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-brass">Today</p>
        <h1 className="font-display text-4xl">Pipeline overview</h1>
      </header>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map(([label, value]) => (
          <Card key={String(label)}>
            <div className="text-sm text-ink/60">{label}</div>
            <div className="mt-2 font-display text-4xl">{value}</div>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <h2 className="font-display text-2xl">Leads by status</h2>
          <div className="mt-4 h-64">
            {chart.length === 0 ? <p className="text-sm text-ink/60">No leads yet.</p> : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart}>
                  <XAxis dataKey="status" tick={{ fontSize: 12 }} />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#9a7340" radius={8} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
        <Card>
          <h2 className="font-display text-2xl">Email processing</h2>
          <ul className="mt-4 space-y-2 text-sm">
            {Object.keys(data.email_stats).length === 0 ? <li className="text-ink/60">No stored messages yet.</li> : null}
            {Object.entries(data.email_stats).map(([status, count]) => (
              <li key={status} className="flex items-center justify-between border-b border-line py-2">
                <Badge tone={statusTone(status)}>{status.replaceAll("_", " ")}</Badge>
                <span>{count}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card>
        <h2 className="font-display text-2xl">Recent agent runs</h2>
        <div className="mt-4 space-y-3">
          {data.recent_runs.length === 0 ? <p className="text-sm text-ink/60">No agent runs yet.</p> : null}
          {data.recent_runs.map((run) => (
            <div key={run.id} className="rounded-2xl border border-line px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <p className="truncate text-sm">{run.goal}</p>
                <Badge tone={statusTone(run.status)}>{run.status}</Badge>
              </div>
              <p className="mt-1 text-xs text-ink/50">{formatWhen(run.created_at)}</p>
              {run.error_message ? <p className="mt-2 text-sm text-red-700">{run.error_message}</p> : null}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
