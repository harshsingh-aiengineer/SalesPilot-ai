# Example agent run

Prompt:

```text
Find hot leads with overdue follow-ups and explain the next best actions.
```

Expected tool sequence, when synthetic seed data is loaded:

1. `list_overdue_follow_ups` or `search_leads` with `priority=hot` and `overdue=true`
2. The tool returns database rows such as Hassan Iqbal and Aisha Rahman, including ids and missing information.
3. The model writes the answer from those rows.

The activity panel shows the tool name, the arguments, and the JSON result. If the model says it emailed someone and `send_approved_email` did not return `provider_accepted`, the reply is corrected to say that no email was sent.

Another prompt:

```text
Schedule a follow-up for lead 3 tomorrow morning.
```

Expected tool: `schedule_follow_up` with `lead_id` and an ISO datetime. The CRM follow-up field changes only if that tool result is `ok: true`.

`send_approved_email` returns `approval_required` until you approve the draft in Email Center. The agent cannot approve its own send.
