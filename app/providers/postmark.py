import logging
import requests

from app.providers.base import BaseProvider, SendResult, ProviderError
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class PostmarkProvider(BaseProvider):
    name = "postmark"

    API_URL = "https://api.postmarkapp.com/email"

    def __init__(self):
        settings = get_settings()
        self._server_token = settings.postmark_server_token
        self._from_email = settings.postmark_from_email

    def send(self, recipient: str, subject: str, body: str) -> SendResult:
        if not self._server_token:
            raise ProviderError(self.name, "POSTMARK_SERVER_TOKEN is not configured")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": self._server_token,
        }

        payload = {
            "From": self._from_email,
            "To": recipient,
            "Subject": subject,
            "TextBody": body,
            "MessageStream": "outbound",
        }

        try:
            response = requests.post(self.API_URL, headers=headers, json=payload, timeout=10)

            if response.status_code != 200:
                error_body = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
                raise ProviderError(
                    self.name,
                    f"HTTP {response.status_code}: {error_body}",
                )

            result_data = response.json()
            message_id = result_data.get("MessageID", "unknown")

            logger.info(
                "postmark_send_success",
                extra={"recipient": recipient, "message_id": message_id},
            )

            return SendResult(
                success=True,
                provider_name=self.name,
                provider_response={
                    "message_id": message_id,
                    "submitted_at": result_data.get("SubmittedAt"),
                    "to": result_data.get("To"),
                },
            )

        except ProviderError:
            raise
        except Exception as exc:
            logger.warning(
                "postmark_send_failed",
                extra={"recipient": recipient, "error": str(exc)},
            )
            raise ProviderError(self.name, str(exc)) from exc
