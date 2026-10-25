"""Run a bounded tool loop against Ollama and persist every step."""

from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy.orm import Session

from app.agent.tools import execute_tool, tool_schemas
from app.config import get_settings
from app.llm import LlmError, LlmLock, chat
from app.models import AgentConversation, AgentMessage, AgentRun, AgentStep
from app.services.audit import write_audit
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are SalesPilot, a local sales operations assistant for blinds and interiors. "
    "Use tools for facts about leads, emails, and tasks. "
    "Never invent a customer's budget, measurements, or deadline. "
    "Never claim an email was sent unless send_approved_email returns status provider_accepted. "
    "If a tool returns an error, report that error. Keep the final answer short."
)

_SEND_CLAIM = re.compile(r"\b(email (has been|was) sent|successfully sent|sent the email|i sent)\b", re.I)
_CREATE_CLAIM = re.compile(r"\b(created (a |the )?lead|lead was created|i created the lead)\b", re.I)


def honesty_guard(text: str, evidence: dict) -> str:
    notes: list[str] = []
    if _SEND_CLAIM.search(text) and not evidence.get("send"):
        notes.append("No Gmail send was confirmed in this run. No email was sent.")
    if _CREATE_CLAIM.search(text) and not evidence.get("create_lead"):
        notes.append("No create_lead tool succeeded in this run, so a new lead was not confirmed.")
    if not notes:
        return text
    return text.rstrip() + "\n\n" + " ".join(notes)


def _history(db: Session, conversation_id: int) -> list[dict]:
    limit = get_settings().max_history_messages
    rows = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation_id, AgentMessage.role.in_(["user", "assistant"]))
        .order_by(AgentMessage.id.desc())
        .limit(limit)
        .all()
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for row in reversed(rows):
        messages.append({"role": row.role, "content": row.content[:4000]})
    return messages


def _cancelled(db: Session, run_id: int) -> bool:
    db.expire_all()
    run = db.get(AgentRun, run_id)
    return bool(run and (run.cancel_requested or run.status == "cancelled"))


def _finish(db: Session, run: AgentRun, status: str, summary: str | None, error: str | None, conversation_id: int | None) -> None:
    run.status = status
    run.result_summary = summary
    run.error_message = error
    run.finished_at = utcnow()
    if conversation_id:
        conversation = db.get(AgentConversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()
            if conversation.title in {"New conversation", "New chat"} and (run.goal or summary):
                conversation.title = (run.goal or summary or "Conversation")[:80]
    if summary and conversation_id and status in {"succeeded", "incomplete"}:
        db.add(AgentMessage(conversation_id=conversation_id, role="assistant", content=summary))
    write_audit(
        db,
        actor_type="agent",
        action="agent.run",
        entity_type="agent_run",
        entity_id=run.id,
        result=status,
        details={"error": error} if error else {"status": status},
    )


def execute_run(db: Session, run_id: int) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"Agent run {run_id} does not exist")
    if run.cancel_requested:
        _finish(db, run, "cancelled", None, "Cancelled before the model was called.", run.conversation_id)
        db.commit()
        return run
    run.status = "running"
    run.started_at = utcnow()
    db.commit()

    settings = get_settings()
    messages = _history(db, run.conversation_id) if run.conversation_id else [{"role": "system", "content": SYSTEM_PROMPT}]
    if not any(item.get("role") == "user" for item in messages):
        messages.append({"role": "user", "content": run.goal[:4000]})

    deadline = time.monotonic() + settings.max_agent_seconds
    used_tools = 0
    evidence = {"send": False, "create_lead": False, "update_lead": False}
    sequence = 0

    while used_tools < settings.max_agent_tool_calls:
        if _cancelled(db, run.id):
            run = db.get(AgentRun, run_id)
            _finish(db, run, "cancelled", None, "Cancelled.", run.conversation_id)
            db.commit()
            return run
        if time.monotonic() > deadline:
            run = db.get(AgentRun, run_id)
            _finish(db, run, "incomplete", None, "The run reached its time limit before the model finished.", run.conversation_id)
            db.commit()
            return run
        try:
            with LlmLock(db, holder=f"run-{run.id}", ttl_seconds=settings.ollama_timeout_seconds + 30):
                response = chat(messages, tools=tool_schemas())
        except LlmError as exc:
            run = db.get(AgentRun, run_id)
            _finish(db, run, "failed", None, exc.message, run.conversation_id)
            db.commit()
            logger.warning("agent run %s failed: %s", run_id, exc.code)
            return run

        message = response.get("message") or {}
        calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": calls,
            }
        )
        if not calls:
            final = content or "The model returned an empty response."
            final = honesty_guard(final, evidence)
            run = db.get(AgentRun, run_id)
            _finish(db, run, "succeeded", final, None, run.conversation_id)
            db.commit()
            return run

        for call in calls:
            if used_tools >= settings.max_agent_tool_calls:
                break
            function = call.get("function") or {}
            name = function.get("name") or "unknown"
            arguments = function.get("arguments") if isinstance(function.get("arguments"), (dict, str)) else {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = None
            used_tools += 1
            sequence += 1
            step = AgentStep(
                agent_run_id=run_id,
                sequence_number=sequence,
                tool_name=str(name)[:100],
                input_summary=json.dumps(arguments, default=str)[:1500] if isinstance(arguments, dict) else str(arguments)[:1500],
                status="running",
            )
            db.add(step)
            db.commit()
            result = execute_tool(db, str(name), arguments)
            if result.get("ok") and name == "send_approved_email" and result.get("status") == "provider_accepted":
                evidence["send"] = True
            if result.get("ok") and name == "create_lead":
                evidence["create_lead"] = True
            if result.get("ok") and name == "update_lead":
                evidence["update_lead"] = True
            step.status = "succeeded" if result.get("ok") else "failed"
            step.result_summary = json.dumps(result, default=str)[:2000]
            step.finished_at = utcnow()
            messages.append({"role": "tool", "name": str(name), "content": json.dumps(result, default=str)[:4000]})
            db.commit()

        if used_tools >= settings.max_agent_tool_calls and calls:
            # Give the model one final chance only when the last response was a tool call
            # and the budget is exhausted. That final call is not allowed to request more tools.
            try:
                with LlmLock(db, holder=f"run-{run.id}-final"):
                    response = chat(messages, tools=None)
            except LlmError as exc:
                run = db.get(AgentRun, run_id)
                _finish(
                    db,
                    run,
                    "incomplete",
                    None,
                    f"Tool-call limit reached and the final summary failed: {exc.message}",
                    run.conversation_id,
                )
                db.commit()
                return run
            content = ((response.get("message") or {}).get("content") or "").strip()
            final = honesty_guard(content or "Stopped after the tool-call limit.", evidence)
            run = db.get(AgentRun, run_id)
            _finish(db, run, "incomplete", final, "Tool-call limit reached.", run.conversation_id)
            db.commit()
            return run

    run = db.get(AgentRun, run_id)
    _finish(db, run, "incomplete", None, "Tool-call limit reached.", run.conversation_id)
    db.commit()
    return run
