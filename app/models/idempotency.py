from datetime import datetime, timezone, timedelta

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _default_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    notification_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    cached_response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_default_expires_at,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<IdempotencyKey key={self.key} notification_id={self.notification_id}>"
