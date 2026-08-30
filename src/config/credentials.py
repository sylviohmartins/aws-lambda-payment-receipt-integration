import json
import logging
import os

import boto3
from botocore.config import Config

from src.client.sts import StsException

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()
except (ImportError, ModuleNotFoundError):  # fallback apenas para execução isolada deste patch
    logger = logging.getLogger(__name__)


_BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)


class AwsSecretManagerConfig:
    """Carrega somente CLIENT_ID e CLIENT_SECRET necessários ao STS."""

    def __init__(self, secret_arn: str, region: str | None = None):
        self.secret_arn = secret_arn
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

    def set_env_from_secret(self) -> None:
        if not self.secret_arn:
            return

        try:
            client = boto3.client(
                "secretsmanager",
                region_name=self.region,
                config=_BOTO_CONFIG,
            )
            response = client.get_secret_value(SecretId=self.secret_arn)
            secret = json.loads(response.get("SecretString", "{}"))
            client_id = secret.get("client_id", "")
            client_secret = secret.get("client_secret", "")

            if not client_id or not client_secret:
                raise StsException("Credenciais ausentes no Secrets Manager")

            os.environ["CLIENT_ID"] = client_id
            os.environ["CLIENT_SECRET"] = client_secret
            logger.info("Credenciais STS carregadas do Secrets Manager")
        except StsException:
            raise
        except Exception as exc:
            logger.error("Falha ao carregar credenciais do Secrets Manager: %s", type(exc).__name__)
            raise StsException("Erro ao carregar credenciais do Secrets Manager") from exc
