# Troubleshooting

## Containers do not start

```powershell
.\scripts\logs.ps1
docker compose ps
```

Confirm Docker Desktop is running. Ports 5173, 8000, and 11434 must be free on `127.0.0.1`.

## API is unhealthy

```powershell
.\scripts\logs.ps1 api
```

The API waits on SQLite under the `app_data` volume. A permissions error on `/data` shows up in the API log. The health endpoint is `http://127.0.0.1:8000/api/v1/health`.

## Frontend loads but every request fails

The Docker frontend proxies `/api` to the `api` service. If you run Vite on the host instead, `vite.config.ts` proxies `/api` to `http://127.0.0.1:8000`. Sign in again after the API restarts because sessions live in the database that was reset.

## Ollama is slow or times out

That is expected on this CPU with `qwen2.5:3b`. See `docs/local-model-setup.md`. Raise `OLLAMA_TIMEOUT_SECONDS` only if generations are succeeding but slowly. The Settings page shows whether that installed model is reachable.

## "Connected" never appears for Gmail

A missing client secret, a redirect URI mismatch, or a failed profile call all stay disconnected. Read the `reason` on the Settings page. The usual redirect URI is `http://127.0.0.1:8000/api/v1/gmail/callback`.

## Sync returns no messages

The default query only matches the `salespilot-test` label. Apply that label in Gmail or change the query in Automation. Sent mail and obvious newsletters are stored as ignored and are not turned into leads.

## A send says the result is unknown

Do not click Send again immediately. Open the draft and click **Reconcile**. SalesPilot searches sent mail for the `X-SalesPilot-Draft` header. It sends again only after you confirm and the previous outcome is not unknown.

## Worker tasks stay queued

```powershell
.\scripts\logs.ps1 worker
```

Only the worker service schedules polls and follow-ups. The API can still queue agent runs in-process. If the worker heartbeat is stale, check that its container is running.

## Duplicate follow-ups

Follow-up tasks use an idempotency key of lead id plus follow-up timestamp. Drafts also respect `follow_up_cooldown_hours`. Restarting the worker does not send the same follow-up again when that key already exists.

## Reset the fictional sample

```powershell
.\scripts\reset-demo-data.ps1
```

That command only removes `synthetic_seed` leads. It does not delete Gmail messages.

## Wipe local data

`docker compose down` keeps volumes. To delete the database and downloaded models:

```powershell
docker compose down -v
```

That removes leads, tokens, and Ollama models. You will need to pull the model and authorize Gmail again.
