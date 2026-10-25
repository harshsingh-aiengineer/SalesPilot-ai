# Local model setup

SalesPilot uses Ollama over its HTTP API. It does not call any paid or hosted model API.

Model: `qwen2.5:3b`

That is the model installed on this machine. The name comes from `OLLAMA_MODEL`.

## Confirm the model

```powershell
ollama list
```

Ready means:

- Ollama lists `qwen2.5:3b`.
- A short chat request returns a reply.

You can also use **Run readiness prompt** on the Settings page. That button performs a real generation. It can take a minute on a CPU.

## This machine

Intel Core i3 11th gen and 16 GB RAM run `qwen2.5:3b`. SalesPilot keeps one model request in flight (`MAX_CONCURRENT_LLM=1`), uses a 4096 token context (`OLLAMA_NUM_CTX`), and caps each agent run (`MAX_AGENT_TOOL_CALLS=8`, `MAX_AGENT_SECONDS=180`).

Expect a tool-using reply to take from under a minute to several minutes. Do not start several agent runs or inbox classifications at once.

Invalid tool arguments are reported back to the model. If the run cannot recover, the run is marked incomplete or failed. SalesPilot does not replace that with a canned success message.

## When Ollama is down

CRM create, edit, search, and delete keep working. Agent runs fail with the Ollama error. Email processing falls back to deterministic rules and records `llm_error` instead of pretending the model ran.
