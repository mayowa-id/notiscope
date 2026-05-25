from sqlalchemy import String, Text, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class NotificationStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), default="email", nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=NotificationStatus.PENDING, nullable=False, index=True
    )
    provider_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Notification id={self.id} status={self.status} recipient={self.recipient}>"
