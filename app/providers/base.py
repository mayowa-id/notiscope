from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SendResult:
    success: bool
    provider_name: str
    provider_response: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class ProviderError(Exception):
    """Raised when a provider fails to send a notification."""
    def __init__(self, provider_name: str, message: str):
        self.provider_name = provider_name
        super().__init__(f"[{provider_name}] {message}")


class BaseProvider(ABC):
    """Abstract base that all notification providers must implement."""

    name: str  # e.g. "sendgrid", "ses"

    @abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> SendResult:
        """
        Send a notification.

        Args:
            recipient: destination address (email, phone number, device token)
            subject:   message subject / title
            body:      message body (plain text; HTML supported for email)

        Returns:
            SendResult with success=True on delivery acceptance.

        Raises:
            ProviderError: if the provider rejects the request or times out.
        """
        ...