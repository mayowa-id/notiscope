"""
ProviderManager — orchestrates primary → fallback delivery logic.
"""
import logging
from app.providers.base import BaseProvider, SendResult, ProviderError

logger = logging.getLogger(__name__)


class ProviderExhaustedException(Exception):
    """Raised when all providers have failed for a given send attempt."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"All providers failed: {'; '.join(errors)}")


class ProviderManager:
    """
    Tries the primary provider first. On failure, falls over to the fallback.
    If both fail, raises ProviderExhaustedException — the worker catches this
    and triggers Celery's retry mechanism.
    """

    def __init__(self, primary: BaseProvider, fallback: BaseProvider):
        self.primary = primary
        self.fallback = fallback

    def send(self, recipient: str, subject: str, body: str) -> SendResult:
        errors: list[str] = []

        # Primary attempt
        try:
            result = self.primary.send(recipient, subject, body)
            logger.info(
                "provider_success",
                extra={"provider": self.primary.name, "recipient": recipient},
            )
            return result
        except ProviderError as exc:
            logger.warning(
                "provider_failed_trying_fallback",
                extra={
                    "primary_provider": self.primary.name,
                    "fallback_provider": self.fallback.name,
                    "error": str(exc),
                    "recipient": recipient,
                },
            )
            errors.append(str(exc))

        # Fallback attempt
        try:
            result = self.fallback.send(recipient, subject, body)
            logger.info(
                "provider_success_via_fallback",
                extra={"provider": self.fallback.name, "recipient": recipient},
            )
            return result
        except ProviderError as exc:
            logger.error(
                "provider_fallback_also_failed",
                extra={
                    "primary_provider": self.primary.name,
                    "fallback_provider": self.fallback.name,
                    "error": str(exc),
                    "recipient": recipient,
                },
            )
            errors.append(str(exc))

        raise ProviderExhaustedException(errors)


def build_provider_manager() -> ProviderManager:
    """
    Factory function. Import this instead of constructing ProviderManager directly.
    This is the single place to swap providers.

    Provider chain: AWS SES (primary) -> Postmark (fallback)
    """
    from app.providers.ses import SESProvider
    from app.providers.postmark import PostmarkProvider

    return ProviderManager(
        primary=SESProvider(),
        fallback=PostmarkProvider(),
    )
