import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.providers.base import BaseProvider, SendResult, ProviderError
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SESProvider(BaseProvider):
    name = "ses"

    def __init__(self):
        settings = get_settings()
        self._from_email = settings.ses_from_email
        self._client = boto3.client(
            "ses",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    def send(self, recipient: str, subject: str, body: str) -> SendResult:
        try:
            response = self._client.send_email(
                Source=self._from_email,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
            )

            message_id = response.get("MessageId", "unknown")

            logger.info(
                "ses_send_success",
                extra={"recipient": recipient, "message_id": message_id},
            )

            return SendResult(
                success=True,
                provider_name=self.name,
                provider_response={
                    "message_id": message_id,
                    "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
                },
            )

        except (BotoCoreError, ClientError) as exc:
            logger.warning(
                "ses_send_failed",
                extra={"recipient": recipient, "error": str(exc)},
            )
            raise ProviderError(self.name, str(exc)) from exc
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}") from exc
