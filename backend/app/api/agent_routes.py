"""Agent conversations and runs."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.runner import execute_run
from app.api.deps import require_user
from app.database import SessionLocal, get_db
from app.models import AgentConversation, AgentMessage, AgentRun
from app.schemas import ChatAccepted, ConversationCreate, ConversationOut, MessageCreate, MessageOut, RunOut
from app.timeutils import utcnow

router = APIRouter()


def _run_in_thread(run_id: int) -> None:
    db = SessionLocal()
    try:
        execute_run(db, run_id)
    finally:
        db.close()


def _start(run_id: int) -> None:
    threading.Thread(target=_run_in_thread, args=(run_id,), name=f"agent-run-{run_id}", daemon=True).start()


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), _: str = Depends(require_user)) -> list[ConversationOut]:
    rows = db.query(AgentConversation).order_by(AgentConversation.updated_at.desc()).limit(100).all()
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, db: Session = Depends(get_db), _: str = Depends(require_user)) -> ConversationOut:
    row = AgentConversation(title=(body.title or "New conversation")[:200])
    db.add(row)
    db.commit()
    db.refresh(row)
    return ConversationOut.model_validate(row)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    row = db.get(AgentConversation, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Conversation not found."})
    messages = db.query(AgentMessage).filter(AgentMessage.conversation_id == conversation_id).order_by(AgentMessage.id.asc()).all()
    runs = db.query(AgentRun).filter(AgentRun.conversation_id == conversation_id).order_by(AgentRun.id.desc()).limit(10).all()
    return {
        "conversation": ConversationOut.model_validate(row).model_dump(mode="json"),
        "messages": [MessageOut.model_validate(message).model_dump(mode="json") for message in messages],
        "runs": [RunOut.model_validate(run).model_dump(mode="json") for run in runs],
    }


@router.post("/conversations/{conversation_id}/messages", response_model=ChatAccepted, status_code=202)
def post_message(conversation_id: int, body: MessageCreate, db: Session = Depends(get_db), _: str = Depends(require_user)) -> ChatAccepted:
    conversation = db.get(AgentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Conversation not found."})
    active = (
        db.query(AgentRun)
        .filter(AgentRun.conversation_id == conversation_id, AgentRun.status.in_(["queued", "running"]))
        .first()
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "run_in_progress", "message": "This conversation already has a run in progress."},
        )
    message = AgentMessage(conversation_id=conversation.id, role="user", content=body.content.strip())
    conversation.updated_at = utcnow()
    run = AgentRun(conversation_id=conversation.id, goal=body.content.strip()[:4000], status="queued")
    db.add(message)
    db.add(run)
    db.commit()
    db.refresh(conversation)
    db.refresh(message)
    db.refresh(run)
    _start(run.id)
    return ChatAccepted(
        conversation=ConversationOut.model_validate(conversation),
        user_message=MessageOut.model_validate(message),
        run=RunOut.model_validate(run),
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> RunOut:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Run not found."})
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> RunOut:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Run not found."})
    if run.status in {"succeeded", "failed", "incomplete", "cancelled"}:
        return RunOut.model_validate(run)
    run.cancel_requested = True
    if run.status == "queued":
        run.status = "cancelled"
        run.finished_at = utcnow()
        run.error_message = "Cancelled before the model was called."
    db.commit()
    db.refresh(run)
    return RunOut.model_validate(run)
