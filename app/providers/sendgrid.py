import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, From, To, Subject, PlainTextContent

from app.providers.base import BaseProvider, SendResult, ProviderError
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SendGridProvider(BaseProvider):
    name = "sendgrid"

    def __init__(self):
        settings = get_settings()
        self._api_key = settings.sendgrid_api_key
        self._from_email = settings.sendgrid_from_email
        self._from_name = settings.sendgrid_from_name
        self._client = SendGridAPIClient(api_key=self._api_key)

    def send(self, recipient: str, subject: str, body: str) -> SendResult:
        if not self._api_key:
            raise ProviderError(self.name, "SENDGRID_API_KEY is not configured")

        message = Mail(
            from_email=From(self._from_email, self._from_name),
            to_emails=To(recipient),
            subject=Subject(subject),
            plain_text_content=PlainTextContent(body),
        )

        try:
            response = self._client.send(message)

            if response.status_code not in (200, 201, 202):
                raise ProviderError(
                    self.name,
                    f"Unexpected status code: {response.status_code} — {response.body}",
                )

            logger.info(
                "sendgrid_send_success",
                extra={"recipient": recipient, "status_code": response.status_code},
            )

            return SendResult(
                success=True,
                provider_name=self.name,
                provider_response={
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                },
            )

        except ProviderError:
            raise
        except Exception as exc:
            logger.warning(
                "sendgrid_send_failed",
                extra={"recipient": recipient, "error": str(exc)},
            )
            raise ProviderError(self.name, str(exc)) from exc
