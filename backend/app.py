"""FastAPI application: chat API, conversations, memories, settings, status, and static UI."""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from . import offline
from .config import ROOT, Settings, SettingsValidationError, load_settings
from .db import (
    Database,
    DatabaseError,
    DuplicateMessageError,
    DuplicateTaskError,
    DuplicateTaskExecutionError,
    InvalidTaskTransitionError,
    NotFoundError,
)
from .engine import Engine, EngineBusy, EngineNotReady, PromptTooLong
from .logging_setup import setup_logging
from .permissions import (
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalRejectedError,
    ApprovalRequiredError,
    OfflineModeError,
    PermissionDeniedError,
)
from .services import (
    ChatService,
    ConfirmationRequiredError,
    ConversationService,
    InvalidIdError,
    MemoryService,
    ModelReloadConfirmationRequired,
    NotificationService,
    SettingsService,
    TaskService,
    ToolService,
)
from .tasks import get_system_timezone_name
from .tools import (
    ToolCancelledError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolValidationError,
    UnknownToolError,
    WorkspaceSecurityError,
)
from .voice import (
    AudioDeviceDisconnectedError,
    AudioDeviceUnavailableError,
    AudioPlaybackError,
    AudioRecordingError,
    EmptyAudioError,
    InvalidAudioFormatError,
    SilentAudioError,
    TranscriptionCancelledError,
    VoiceError,
    VoiceModelMissingError,
    VoiceService,
)

log = logging.getLogger("jarvis.api")
FRONTEND_DIR = ROOT / "frontend"
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


# ---- request schemas --------------------------------------------------------
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=32_000)

    @field_validator("content")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v


class ChatRequest(BaseModel):
    messages: list[Message] | None = Field(default=None, min_length=1, max_length=200)
    message: str | None = Field(default=None, min_length=1, max_length=32_000)
    stream: bool | None = None
    max_tokens: int | None = Field(None, ge=1, le=8192)
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, gt=0, le=1)
    conversation_id: str | None = Field(None, pattern=r"^[0-9a-f]{32}$")
    client_msg_id: str | None = Field(None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def normalize_messages(self):
        if self.messages is None:
            if self.message is None or not self.message.strip():
                raise ValueError("either 'messages' or a non-blank 'message' is required")
            self.messages = [Message(role="user", content=self.message)]
        if self.messages[-1].role != "user":
            raise ValueError("the last message must have role 'user'")
        return self


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None

    @model_validator(mode="after")
    def has_at_least_one_field(self):
        if self.title is None and self.archived is None:
            raise ValueError("provide 'title' and/or 'archived'")
        if self.title is not None and not self.title.strip():
            raise ValueError("title must not be blank")
        return self


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="general", min_length=1, max_length=40)
    importance: int = Field(default=3, ge=1, le=5)
    source_conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=40)
    importance: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def has_update(self):
        if self.content is None and self.category is None and self.importance is None:
            raise ValueError("provide at least one of 'content', 'category', or 'importance'")
        if self.content is not None and not self.content.strip():
            raise ValueError("content must not be blank")
        return self


class ReloadModelRequest(BaseModel):
    confirm: bool = False


class ToolExecuteRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    approval_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ApprovalDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    task_type: Literal["reminder", "local_task", "research_task", "followup"] = "reminder"
    priority: int = Field(default=3, ge=1, le=5)
    scheduled_at: str | None = Field(default=None, max_length=64)
    deadline_at: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    recurrence: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    max_retries: int = Field(default=2, ge=0, le=20)
    timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    approval_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    initial_state: str | None = Field(default=None, max_length=32)
    metadata: dict[str, Any] | None = None


class TaskPauseRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class TaskCancelRequest(BaseModel):
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=300)


class TaskSnoozeRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=10080)
    until: str | None = Field(default=None, max_length=64)


class NotificationReadRequest(BaseModel):
    read: bool = True


class VoiceRecordRequest(BaseModel):
    duration_seconds: float | None = Field(default=None, ge=0.2, le=60.0)
    device: str | None = Field(default=None, max_length=256)


class VoiceTranscribeRequest(BaseModel):
    audio_base64: str = Field(min_length=1, max_length=25_000_000)
    raise_on_silence: bool = False


class VoiceTranscriptEditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class VoiceSpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    voice_id: str | None = Field(default=None, max_length=128)
    speaker: str | None = Field(default=None, max_length=256)
    speed: float | None = Field(default=None, ge=0.5, le=2.0)
    volume: float | None = Field(default=None, ge=0.0, le=2.0)
    force: bool = True


class VoiceWakeWordRequest(BaseModel):
    enabled: bool
    sensitivity: float | None = Field(default=None, ge=0.05, le=0.99)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=120.0)
    start_background_stream: bool = False


def _host_only(host_header: str) -> str:
    """Strip the port from a Host header ("127.0.0.1:8000" -> "127.0.0.1", "[::1]:80" -> "[::1]")."""
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0]


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               load_model: bool = True, voice_service: VoiceService | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = engine or Engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.resolve(settings.log_dir), settings.log_level)
        if settings.enforce_offline:
            offline.enable()
        db = Database(settings.resolve(settings.db_path))
        settings_svc = SettingsService(db, engine, settings)
        memory_svc = MemoryService(db, settings_svc)
        conv_svc = ConversationService(db)
        tool_svc = ToolService(db, engine, settings_svc)
        notif_svc = NotificationService(db)
        task_svc = TaskService(db, engine, settings_svc, tool_svc)
        voice_svc = voice_service or VoiceService(lambda: settings_svc.active_settings)
        settings_svc.voice_service = voice_svc
        chat_svc = ChatService(
            db,
            engine,
            settings_svc,
            memory_svc,
            tool_service=tool_svc,
            task_service=task_svc,
            voice_service=voice_svc,
        )

        app.state.db = db
        app.state.settings_service = settings_svc
        app.state.memory_service = memory_svc
        app.state.conversation_service = conv_svc
        app.state.tool_service = tool_svc
        app.state.notification_service = notif_svc
        app.state.task_service = task_svc
        app.state.voice_service = voice_svc
        app.state.chat_service = chat_svc

        log.info("starting", extra={"host": settings.host, "port": settings.port,
                                    "offline_enforced": offline.is_enabled()})
        task_svc.start_scheduler()
        if load_model:
            engine.load_in_background()
        try:
            yield
        finally:
            log.info("shutting down")
            voice_svc.close()
            task_svc.stop_scheduler()
            await run_in_threadpool(engine.close)
            db.close()
            log.info("shutdown complete")

    app = FastAPI(title="Jarvis", version="0.5.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.engine = engine
    app.state.settings = settings

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """Defence in depth on top of binding to 127.0.0.1: reject non-loopback clients and
        foreign Host headers (DNS-rebinding protection) unless allow_remote is set."""
        if not settings.allow_remote:
            client = request.client.host if request.client else None
            if not _is_loopback(client) or not _is_loopback(_host_only(request.headers.get("host", ""))):
                return JSONResponse({"detail": "Jarvis only accepts local connections"}, status_code=403)
        return await call_next(request)

    def _service_http_error(e: Exception) -> HTTPException:
        if isinstance(e, InvalidIdError):
            return HTTPException(400, str(e))
        if isinstance(e, NotFoundError):
            return HTTPException(404, str(e))
        if isinstance(e, (DuplicateMessageError, DuplicateTaskError, DuplicateTaskExecutionError, InvalidTaskTransitionError)):
            return HTTPException(409, str(e))
        if isinstance(e, ModelReloadConfirmationRequired):
            return HTTPException(
                409,
                {
                    "message": str(e),
                    "reload_required": True,
                    "reload_fields": e.fields,
                },
            )
        if isinstance(e, ConfirmationRequiredError):
            return HTTPException(400, str(e))
        if isinstance(e, UnknownToolError):
            return HTTPException(404, str(e))
        if isinstance(e, (WorkspaceSecurityError, ToolValidationError)):
            return HTTPException(422, str(e))
        if isinstance(e, ApprovalExpiredError):
            return HTTPException(410, str(e))
        if isinstance(e, (ApprovalRejectedError, ApprovalMismatchError, OfflineModeError, PermissionDeniedError)):
            return HTTPException(403, str(e))
        if isinstance(e, ToolTimeoutError):
            return HTTPException(504, str(e))
        if isinstance(e, (ToolCancelledError, TranscriptionCancelledError)):
            return HTTPException(409, str(e))
        if isinstance(e, VoiceModelMissingError):
            return HTTPException(503, str(e))
        if isinstance(e, (EmptyAudioError, InvalidAudioFormatError, SilentAudioError)):
            return HTTPException(422, str(e))
        if isinstance(e, (AudioDeviceUnavailableError, AudioDeviceDisconnectedError)):
            return HTTPException(400, str(e))
        if isinstance(e, (AudioRecordingError, AudioPlaybackError, VoiceError)):
            return HTTPException(400, str(e))
        if isinstance(e, ToolExecutionError):
            return HTTPException(400, str(e))
        if isinstance(e, PromptTooLong):
            return HTTPException(400, str(e))
        if isinstance(e, (SettingsValidationError, ValueError)):
            return HTTPException(422, str(e))
        if isinstance(e, DatabaseError):
            log.error("database error", extra={"error": str(e)})
            return HTTPException(500, "Database operation failed; see logs/jarvis.log")
        if isinstance(e, EngineNotReady):
            return HTTPException(503, f"Model not ready: {e}")
        if isinstance(e, EngineBusy):
            return HTTPException(429, str(e))
        log.exception("unexpected error", exc_info=e)
        return HTTPException(500, "Internal error; see logs/jarvis.log for details")

    # ---- status & health ----------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "model_state": engine.status.state}

    @app.get("/api/status")
    def status(request: Request):
        settings_svc: SettingsService | None = getattr(request.app.state, "settings_service", None)
        tool_svc: ToolService | None = getattr(request.app.state, "tool_service", None)
        notif_svc: NotificationService | None = getattr(request.app.state, "notification_service", None)
        task_svc: TaskService | None = getattr(request.app.state, "task_service", None)
        voice_svc: VoiceService | None = getattr(request.app.state, "voice_service", None)
        active = settings_svc.active_settings if settings_svc else settings
        pending_approvals = len(tool_svc.list_approvals(status="pending", limit=50)) if tool_svc else 0
        unread_notifications = notif_svc.unread_count() if notif_svc else 0
        active_tasks = len(task_svc.list_tasks(filter_group="pending", limit=100)) if task_svc else 0
        return {
            **engine.status.__dict__,
            "offline_enforced": offline.is_enabled(),
            "offline_mode": active.offline_mode,
            "tools_enabled": active.tools_enabled,
            "pending_approvals_count": pending_approvals,
            "unread_notifications_count": unread_notifications,
            "active_tasks_count": active_tasks,
            "voice_state": voice_svc.state if voice_svc else "Idle",
            "wake_word_enabled": voice_svc.wake_word.enabled if voice_svc else False,
            "voice_input_enabled": active.voice_input_enabled,
            "voice_output_enabled": active.voice_output_enabled,
            "system_timezone": get_system_timezone_name(),
            "blocked_network_attempts": len(offline.blocked_attempts),
            "reload_required": settings_svc.get_state()["reload_required"] if settings_svc else False,
            "pending_reload_fields": settings_svc.pending_reload_fields() if settings_svc else [],
            "settings": {k: getattr(active, k) for k in (
                "n_gpu_layers", "offload_kqv", "flash_attn", "n_ctx", "n_batch",
                "max_tokens", "temperature", "top_p", "repeat_penalty",
                "theme", "stream_responses", "conversation_history_enabled",
                "memory_enabled", "default_new_conversation_behavior",
                "offline_mode", "tools_enabled", "max_tool_calls_per_turn",
                "scheduler_enabled", "scheduler_poll_seconds", "max_concurrent_tasks",
                "voice_input_enabled", "voice_output_enabled", "wake_word_enabled",
                "selected_microphone", "selected_speaker", "selected_tts_voice",
                "speech_speed", "speech_volume", "listening_duration",
                "wake_word_sensitivity", "wake_word_timeout_seconds",
                "auto_submit_transcript", "stop_speaking_on_input")},
        }

    # ---- conversations ------------------------------------------------------
    @app.post("/api/conversations", status_code=201)
    def create_conversation(req: ConversationCreateRequest | None = None, request: Request = None):
        svc: ConversationService = request.app.state.conversation_service
        try:
            return svc.create_conversation(title=req.title if req else None)
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/conversations")
    def list_conversations(
        request: Request,
        include_archived: bool = False,
        archived_only: bool = False,
        limit: int = Query(default=100, ge=1, le=500),
    ):
        svc: ConversationService = request.app.state.conversation_service
        try:
            return {
                "conversations": svc.list_conversations(
                    include_archived=include_archived, archived_only=archived_only, limit=limit
                )
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/conversations/search")
    @app.get("/api/search")
    def search_conversations(
        request: Request,
        q: str = Query(default="", max_length=300),
        include_archived: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        svc: ConversationService = request.app.state.conversation_service
        try:
            return {
                "query": q,
                "conversations": svc.search_conversations(q, include_archived=include_archived, limit=limit),
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request):
        svc: ConversationService = request.app.state.conversation_service
        try:
            return svc.get_conversation(conversation_id, require_exists=True)
        except Exception as e:
            raise _service_http_error(e)

    @app.patch("/api/conversations/{conversation_id}")
    @app.put("/api/conversations/{conversation_id}")
    def update_conversation(conversation_id: str, req: ConversationUpdateRequest, request: Request):
        svc: ConversationService = request.app.state.conversation_service
        try:
            conv = svc.update_conversation(conversation_id, title=req.title, archived=req.archived)
            return {"conversation": conv}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/conversations/{conversation_id}/archive")
    def archive_conversation(conversation_id: str, request: Request):
        svc: ConversationService = request.app.state.conversation_service
        try:
            return {"conversation": svc.update_conversation(conversation_id, archived=True)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/conversations/{conversation_id}/restore")
    def restore_conversation(conversation_id: str, request: Request):
        svc: ConversationService = request.app.state.conversation_service
        try:
            return {"conversation": svc.update_conversation(conversation_id, archived=False)}
        except Exception as e:
            raise _service_http_error(e)

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(
        conversation_id: str,
        request: Request,
        confirm: bool = Query(default=False),
    ):
        svc: ConversationService = request.app.state.conversation_service
        try:
            svc.delete_conversation(conversation_id, confirm=confirm)
            return {"deleted": True, "conversation_id": conversation_id}
        except Exception as e:
            raise _service_http_error(e)

    # ---- settings -----------------------------------------------------------
    @app.get("/api/settings")
    def get_settings(request: Request):
        svc: SettingsService = request.app.state.settings_service
        return svc.get_state()

    @app.put("/api/settings")
    @app.patch("/api/settings")
    async def update_settings(body: dict[str, Any], request: Request):
        svc: SettingsService = request.app.state.settings_service
        payload = dict(body or {})
        confirm_reload = bool(payload.pop("confirm_model_reload", False))
        apply_reload = bool(payload.pop("apply_model_reload", False))
        save_only = bool(payload.pop("save_only", False))
        updates = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
        try:
            return await run_in_threadpool(
                svc.update_settings,
                updates,
                confirm_model_reload=confirm_reload,
                apply_model_reload=apply_reload,
                save_only=save_only,
            )
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/settings/reload-model")
    async def reload_model(req: ReloadModelRequest, request: Request):
        svc: SettingsService = request.app.state.settings_service
        try:
            return await run_in_threadpool(svc.reload_model, confirm=req.confirm)
        except Exception as e:
            raise _service_http_error(e)

    # ---- explicit long-term memories ----------------------------------------
    @app.get("/api/memories")
    def list_memories(
        request: Request,
        category: str | None = None,
        q: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        svc: MemoryService = request.app.state.memory_service
        try:
            return {"memories": svc.list_memories(category=category, query=q, limit=limit)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/memories", status_code=201)
    def create_memory(req: MemoryCreateRequest, request: Request):
        svc: MemoryService = request.app.state.memory_service
        try:
            mem = svc.create_memory(
                content=req.content,
                category=req.category,
                importance=req.importance,
                source_conversation_id=req.source_conversation_id,
            )
            return {"memory": mem}
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/memories/{memory_id}")
    def get_memory(memory_id: str, request: Request):
        svc: MemoryService = request.app.state.memory_service
        try:
            return {"memory": svc.get_memory(memory_id)}
        except Exception as e:
            raise _service_http_error(e)

    @app.patch("/api/memories/{memory_id}")
    @app.put("/api/memories/{memory_id}")
    def update_memory(memory_id: str, req: MemoryUpdateRequest, request: Request):
        svc: MemoryService = request.app.state.memory_service
        try:
            mem = svc.update_memory(
                memory_id, content=req.content, category=req.category, importance=req.importance
            )
            return {"memory": mem}
        except Exception as e:
            raise _service_http_error(e)

    @app.delete("/api/memories/{memory_id}")
    def delete_memory(memory_id: str, request: Request):
        svc: MemoryService = request.app.state.memory_service
        try:
            svc.delete_memory(memory_id)
            return {"deleted": True, "memory_id": memory_id}
        except Exception as e:
            raise _service_http_error(e)

    # ---- tools, workspace & approvals (Phase 3) -----------------------------
    @app.get("/api/tools")
    def list_tools_and_activity(
        request: Request,
        conversation_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        tool_svc: ToolService = request.app.state.tool_service
        settings_svc: SettingsService = request.app.state.settings_service
        try:
            return {
                "tools": tool_svc.list_tools(),
                "executions": tool_svc.list_executions(conversation_id=conversation_id, limit=limit),
                "pending_approvals": tool_svc.list_approvals(
                    conversation_id=conversation_id, status="pending", limit=limit
                ),
                "workspace": tool_svc.list_workspace_files(),
                "offline_mode": settings_svc.active_settings.offline_mode,
                "tools_enabled": settings_svc.active_settings.tools_enabled,
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tools/execute")
    async def execute_tool(req: ToolExecuteRequest, request: Request):
        tool_svc: ToolService = request.app.state.tool_service
        try:
            res = await run_in_threadpool(
                tool_svc.execute_direct,
                req.tool_name,
                req.arguments,
                conversation_id=req.conversation_id,
                approval_id=req.approval_id,
                user_initiated_network=True,
            )
            return res
        except ApprovalRequiredError as e:
            return JSONResponse(
                {
                    "status": "awaiting_approval",
                    "pending_approval": e.approval,
                },
                status_code=202,
            )
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tools/cancel")
    def cancel_active_tools(request: Request):
        tool_svc: ToolService = request.app.state.tool_service
        return tool_svc.cancel_active()

    @app.get("/api/workspace/files")
    def list_workspace_files(
        request: Request,
        subdirectory: str = Query(default="", max_length=200),
    ):
        tool_svc: ToolService = request.app.state.tool_service
        try:
            return tool_svc.list_workspace_files(subdirectory=subdirectory)
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/approvals")
    def list_approvals(
        request: Request,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        tool_svc: ToolService = request.app.state.tool_service
        try:
            return {
                "approvals": tool_svc.list_approvals(
                    conversation_id=conversation_id, status=status, limit=limit
                )
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/approvals/{approval_id}")
    def get_approval(approval_id: str, request: Request):
        tool_svc: ToolService = request.app.state.tool_service
        try:
            return {"approval": tool_svc.get_approval(approval_id)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve_tool_call(approval_id: str, request: Request):
        tool_svc: ToolService = request.app.state.tool_service
        chat_svc: ChatService = request.app.state.chat_service
        try:
            return await run_in_threadpool(
                tool_svc.approve_and_execute, approval_id, chat_service=chat_svc
            )
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/approvals/{approval_id}/reject")
    async def reject_tool_call(
        approval_id: str,
        request: Request,
        req: ApprovalDecisionRequest | None = None,
    ):
        tool_svc: ToolService = request.app.state.tool_service
        try:
            return await run_in_threadpool(
                tool_svc.reject_approval,
                approval_id,
                reason=req.reason if req else None,
            )
        except Exception as e:
            raise _service_http_error(e)

    # ---- tasks, scheduler & notifications (Phase 4) -------------------------
    @app.get("/api/tasks")
    def list_tasks(
        request: Request,
        state: str | None = None,
        filter_group: str | None = None,
        task_type: str | None = None,
        conversation_id: str | None = None,
        include_executions: bool = False,
        limit: int = Query(default=200, ge=1, le=500),
    ):
        task_svc: TaskService = request.app.state.task_service
        try:
            tasks = task_svc.list_tasks(
                state=state,
                filter_group=filter_group,
                task_type=task_type,
                conversation_id=conversation_id,
                include_executions=include_executions,
                limit=limit,
            )
            return {
                "tasks": tasks,
                "system_timezone": get_system_timezone_name(),
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks", status_code=201)
    async def create_task(req: TaskCreateRequest, request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            created = await run_in_threadpool(
                task_svc.create_task,
                title=req.title,
                description=req.description,
                task_type=req.task_type,
                priority=req.priority,
                scheduled_at=req.scheduled_at,
                deadline_at=req.deadline_at,
                timezone_name=req.timezone,
                recurrence=req.recurrence,
                action=req.action,
                max_retries=req.max_retries,
                timeout_seconds=req.timeout_seconds,
                approval_id=req.approval_id,
                conversation_id=req.conversation_id,
                initial_state=req.initial_state,
                metadata=req.metadata,
            )
            return {"task": created}
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"task": task_svc.get_task(task_id, include_executions=True)}
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/tasks/{task_id}/executions")
    def list_task_executions(
        task_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        task_svc: TaskService = request.app.state.task_service
        try:
            task_svc.get_task(task_id, include_executions=False)
            return {"executions": task_svc.list_executions(task_id=task_id, limit=limit)}
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/task-executions")
    def list_all_task_executions(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"executions": task_svc.list_executions(limit=limit)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/pause")
    def pause_task(
        task_id: str,
        request: Request,
        req: TaskPauseRequest | None = None,
    ):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"task": task_svc.pause_task(task_id, reason=req.reason if req else None)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/resume")
    def resume_task(task_id: str, request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"task": task_svc.resume_task(task_id)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(
        task_id: str,
        request: Request,
        req: TaskCancelRequest | None = None,
        confirm: bool = Query(default=False),
    ):
        task_svc: TaskService = request.app.state.task_service
        eff_confirm = bool(confirm or (req.confirm if req else False))
        try:
            return {
                "task": task_svc.cancel_task(
                    task_id,
                    confirm=eff_confirm,
                    reason=req.reason if req else None,
                )
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/complete")
    def complete_task(task_id: str, request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"task": task_svc.complete_or_dismiss(task_id)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/snooze")
    def snooze_task(
        task_id: str,
        request: Request,
        req: TaskSnoozeRequest | None = None,
    ):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {
                "task": task_svc.snooze_task(
                    task_id,
                    minutes=req.minutes if req else 10,
                    until=req.until if req else None,
                )
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/approve")
    def approve_task_action(task_id: str, request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"task": task_svc.approve_task(task_id)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/reject")
    def reject_task_action(
        task_id: str,
        request: Request,
        req: ApprovalDecisionRequest | None = None,
    ):
        task_svc: TaskService = request.app.state.task_service
        try:
            return {"task": task_svc.reject_task(task_id, reason=req.reason if req else None)}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/tasks/{task_id}/run")
    async def run_task_now(task_id: str, request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            return await run_in_threadpool(task_svc.run_task_now, task_id)
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/scheduler/tick")
    async def trigger_scheduler_tick(request: Request):
        task_svc: TaskService = request.app.state.task_service
        try:
            return await run_in_threadpool(task_svc.tick_scheduler)
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/notifications")
    def list_notifications(
        request: Request,
        unread_only: bool = False,
        include_dismissed: bool = False,
        task_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ):
        notif_svc: NotificationService = request.app.state.notification_service
        try:
            return {
                "notifications": notif_svc.list_notifications(
                    unread_only=unread_only,
                    include_dismissed=include_dismissed,
                    task_id=task_id,
                    limit=limit,
                ),
                "unread_count": notif_svc.unread_count(),
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/notifications/read-all")
    def mark_all_notifications_read(request: Request):
        notif_svc: NotificationService = request.app.state.notification_service
        try:
            res = notif_svc.mark_all_read()
            return {**res, "unread_count": notif_svc.unread_count()}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/notifications/{notification_id}/read")
    def mark_notification_read(
        notification_id: str,
        request: Request,
        req: NotificationReadRequest | None = None,
    ):
        notif_svc: NotificationService = request.app.state.notification_service
        try:
            notif = notif_svc.mark_read(notification_id, read=req.read if req else True)
            return {"notification": notif, "unread_count": notif_svc.unread_count()}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/notifications/{notification_id}/dismiss")
    def dismiss_notification(notification_id: str, request: Request):
        notif_svc: NotificationService = request.app.state.notification_service
        try:
            notif = notif_svc.dismiss(notification_id)
            return {"notification": notif, "unread_count": notif_svc.unread_count()}
        except Exception as e:
            raise _service_http_error(e)

    # ---- Phase 5: Local Voice Input, Speech Output & Wake-Word --------------
    @app.get("/api/voice/status")
    def get_voice_status(request: Request):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            return voice_svc.get_status()
        except Exception as e:
            raise _service_http_error(e)

    @app.get("/api/voice/devices")
    def get_voice_devices(request: Request):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            return voice_svc.get_devices()
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/voice/record")
    async def record_and_transcribe_voice(
        request: Request,
        req: VoiceRecordRequest | None = None,
    ):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            return await run_in_threadpool(
                lambda: voice_svc.record_and_transcribe(
                    duration_seconds=req.duration_seconds if req else None,
                    device_selection=req.device if req else None,
                )
            )
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/voice/transcribe")
    async def transcribe_voice_audio(request: Request, req: VoiceTranscribeRequest):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            try:
                wav_bytes = base64.b64decode(req.audio_base64, validate=True)
            except Exception as e:
                raise InvalidAudioFormatError(f"Invalid base64 audio payload: {e}") from e
            return await run_in_threadpool(
                lambda: voice_svc.transcribe_audio_bytes(
                    wav_bytes,
                    raise_on_silence=req.raise_on_silence,
                )
            )
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/voice/transcript/edit")
    def edit_voice_transcript(request: Request, req: VoiceTranscriptEditRequest):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            updated = voice_svc.edit_pending_transcript(req.text)
            return {"pending_transcript": updated, "state": voice_svc.state}
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/voice/stop-recording")
    def stop_voice_recording(request: Request):
        voice_svc: VoiceService = request.app.state.voice_service
        voice_svc.stop_recording()
        return voice_svc.get_status()

    @app.post("/api/voice/cancel")
    def cancel_voice_recording(request: Request):
        voice_svc: VoiceService = request.app.state.voice_service
        return voice_svc.cancel_recording()

    @app.post("/api/voice/speak")
    async def speak_voice_text(request: Request, req: VoiceSpeakRequest):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            return await run_in_threadpool(
                lambda: voice_svc.speak_response(
                    req.text,
                    speaker_selection=req.speaker,
                    voice_id=req.voice_id,
                    speed=req.speed,
                    volume=req.volume,
                    force=req.force,
                )
            )
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/voice/synthesize")
    async def synthesize_voice_wav(request: Request, req: VoiceSpeakRequest):
        voice_svc: VoiceService = request.app.state.voice_service
        try:
            syn = await run_in_threadpool(
                lambda: voice_svc.synthesize_speech(
                    req.text,
                    voice_id=req.voice_id,
                    speed=req.speed,
                    volume=req.volume,
                )
            )
            return {
                "audio_base64": base64.b64encode(syn["wav_bytes"]).decode("ascii"),
                "sample_rate": syn["sample_rate"],
                "duration_seconds": syn["duration_seconds"],
                "chunk_count": syn["chunk_count"],
                "chunks": syn["chunks"],
                "synthesis_ms": syn["synthesis_ms"],
            }
        except Exception as e:
            raise _service_http_error(e)

    @app.post("/api/voice/stop-speaking")
    def stop_voice_speaking(request: Request):
        voice_svc: VoiceService = request.app.state.voice_service
        return voice_svc.stop_speaking()

    @app.post("/api/voice/wake-word")
    def configure_wake_word(request: Request, req: VoiceWakeWordRequest):
        voice_svc: VoiceService = request.app.state.voice_service
        settings_svc: SettingsService = request.app.state.settings_service
        try:
            updates: dict[str, Any] = {"wake_word_enabled": req.enabled}
            if req.sensitivity is not None:
                updates["wake_word_sensitivity"] = req.sensitivity
            if req.timeout_seconds is not None:
                updates["wake_word_timeout_seconds"] = req.timeout_seconds
            settings_svc.update_settings(updates)
            status = voice_svc.set_wake_word(
                req.enabled,
                sensitivity=req.sensitivity,
                timeout_seconds=req.timeout_seconds,
                start_background_stream=req.start_background_stream,
            )
            return status
        except Exception as e:
            raise _service_http_error(e)

    # ---- chat completion ----------------------------------------------------
    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request):
        chat_svc: ChatService = request.app.state.chat_service
        settings_svc: SettingsService = request.app.state.settings_service
        msgs = [m.model_dump() for m in req.messages]
        stream = req.stream if req.stream is not None else settings_svc.active_settings.stream_responses

        try:
            turn = await run_in_threadpool(
                chat_svc.prepare_turn,
                messages=msgs,
                conversation_id=req.conversation_id,
                client_msg_id=req.client_msg_id,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                top_p=req.top_p,
            )
        except Exception as e:
            raise _service_http_error(e)

        headers = {"X-Conversation-Id": turn.conversation_id}

        if not stream:
            try:
                enriched = await run_in_threadpool(chat_svc.execute_turn, turn)
            except Exception as e:
                raise _service_http_error(e)
            return JSONResponse(enriched, headers=headers)

        try:  # pull the first event so setup errors become proper HTTP status codes
            first, gen = await run_in_threadpool(chat_svc.start_stream_turn, turn)
        except Exception as e:
            raise _service_http_error(e)

        def sse():
            try:
                for event in chat_svc.iter_stream_turn(turn, first, gen):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                log.exception("streaming failed", exc_info=e)
                yield f"data: {json.dumps({'error': 'Generation failed; see logs/jarvis.log'})}\n\n"
            finally:
                if hasattr(gen, "close"):
                    gen.close()

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={**headers, "Cache-Control": "no-cache"})

    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
