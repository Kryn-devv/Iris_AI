"""SQLAlchemy ORM models for IRIS persistence."""

from datetime import datetime
from typing import Optional, Any
import json
from sqlalchemy import String, Text, DateTime, Integer, Float, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column
from iris.app.database.database import Base


class TaskModel(Base):
    """ORM representation of a IRIS Task."""
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class MemoryRecordModel(Base):
    """ORM model for persistent memory entries."""
    __tablename__ = "memory_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    memory_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # "working", "conversation", "episodic", "semantic", "project"
    key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), default="HIGH", nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    last_accessed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class ToolExecutionLogModel(Base):
    """Audit log for tool executions."""
    __tablename__ = "tool_execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execution_time_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class ReminderModel(Base):
    """Persistent reminders, timers and recurring routines."""
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="reminder", nullable=False)  # "reminder" | "timer" | "routine"
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    recurrence: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # None | "daily" | "weekly" | "weekdays" | "hourly"
    status: Mapped[str] = mapped_column(String(16), default="scheduled", nullable=False, index=True)  # scheduled | fired | cancelled
    channel: Mapped[str] = mapped_column(String(32), default="all", nullable=False)  # where to announce
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    fired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
