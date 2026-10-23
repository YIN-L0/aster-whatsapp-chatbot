"""ORM models for the demo build (a subset of docs/DATA_MODEL.md).

Raw data (messages), structured data (journeys, journal, symptoms) and AI reasoning
(agent_runs, pending_actions) live in separate tables.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy import DateTime as _DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class DateTime(TypeDecorator):
    """Always stores UTC and always returns timezone-aware UTC datetimes (SQLite drops tzinfo otherwise)."""

    impl = _DateTime(timezone=True)
    cache_ok = True

    def __init__(self, timezone: bool = True) -> None:  # signature-compatible with sqlalchemy.DateTime
        super().__init__()

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    wa_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    first_name: Mapped[str | None] = mapped_column(String(80))
    # Asked once before the first real booking
    full_name: Mapped[str | None] = mapped_column(String(120))
    birth_date: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD
    phone: Mapped[str | None] = mapped_column(String(20))  # E.164, only needed for web-chat users
    timezone: Mapped[str] = mapped_column(String(64), default="America/Indiana/Indianapolis")
    # ai | human (handoff in progress) | paused (user sent STOP for proactive messages)
    conversation_mode: Mapped[str] = mapped_column(String(16), default="ai")
    onboarding_state: Mapped[str] = mapped_column(String(24), default="new")  # new|consent_pending|declined|active
    health_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    proactive_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    is_test_user: Mapped[bool] = mapped_column(Boolean, default=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(4))  # in | out
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    type: Mapped[str] = mapped_column(String(24), default="text")  # text|button_reply|template|buttons|media|system
    body: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON)
    sender: Mapped[str] = mapped_column(String(16), default="user")  # user|aster_ai|aster_system|staff
    status: Mapped[str] = mapped_column(String(16), default="received")  # received|processed|ignored
    delivery_status: Mapped[str | None] = mapped_column(String(16))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    checkin_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Journey(TimestampMixin, Base):
    __tablename__ = "journeys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    treatment_slug: Mapped[str] = mapped_column(String(64))
    treatment_label_raw: Mapped[str] = mapped_column(String(200))
    treatment_date: Mapped[date] = mapped_column(Date)
    area: Mapped[str | None] = mapped_column(String(120))
    product: Mapped[str | None] = mapped_column(String(120))
    provider_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|completed|cancelled
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    protocol_version: Mapped[str | None] = mapped_column(String(80))
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    source: Mapped[str] = mapped_column(String(24), default="user_reported")  # user_reported|demo_seed


class PendingAction(TimestampMixin, Base):
    __tablename__ = "pending_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # create_journey|update_journey
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="awaiting_user")
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JournalEntry(TimestampMixin, Base):
    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    checkin_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    day_post_treatment: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    patient_quote: Mapped[str | None] = mapped_column(Text)
    mood: Mapped[str | None] = mapped_column(String(16))
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    extracted_by: Mapped[str] = mapped_column(String(8), default="ai")
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_message_ids: Mapped[list | None] = mapped_column(JSON)


class SymptomObservation(TimestampMixin, Base):
    __tablename__ = "symptom_observations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    symptom_code: Mapped[str] = mapped_column(String(48))
    present: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str | None] = mapped_column(String(16))  # none|mild|moderate|severe
    frequency: Mapped[str | None] = mapped_column(String(64))
    body_area: Mapped[str | None] = mapped_column(String(64))
    status_change: Mapped[str | None] = mapped_column(String(16))  # new|improving|same|worsening|resolved
    computed_trend: Mapped[str | None] = mapped_column(String(16))
    free_text: Mapped[str | None] = mapped_column(Text)
    day_post_treatment: Mapped[int] = mapped_column(Integer)


class Checkin(TimestampMixin, Base):
    __tablename__ = "checkins"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    journey_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"))
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(24), default="recovery")  # recovery|appointment_reminder
    protocol_step: Mapped[int | None] = mapped_column(Integer)
    day_offset: Mapped[int | None] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|sent|answered|missed|cancelled
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Appointment(TimestampMixin, Base):
    """A booking. source=demo_mock for mock slots, aster_api for bookings made in the booking backend."""

    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    journey_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(String(32))  # consultation|follow_up|new_treatment
    provider_name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed|pending
    source: Mapped[str] = mapped_column(String(16), default="demo_mock")
    service: Mapped[str | None] = mapped_column(String(64))
    external_ref: Mapped[str | None] = mapped_column(String(64))  # the booking backend's appointment ref


class Handoff(TimestampMixin, Base):
    __tablename__ = "handoffs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    journey_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(String(32))
    urgency: Mapped[str] = mapped_column(String(16))  # routine|urgent|emergency
    status: Mapped[str] = mapped_column(String(16), default="open")
    summary: Mapped[str] = mapped_column(Text)
    risk_signals: Mapped[dict | None] = mapped_column(JSON)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # main|checkin_opener
    provider: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(24))
    tool_calls: Mapped[list | None] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    risk_rule: Mapped[int] = mapped_column(Integer, default=0)
    risk_agent: Mapped[int] = mapped_column(Integer, default=0)
    risk_final: Mapped[int] = mapped_column(Integer, default=0)
    postcheck_violations: Mapped[list | None] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
