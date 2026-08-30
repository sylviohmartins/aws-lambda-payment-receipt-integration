"""Carregamento de credenciais STS.

O caminho permanente continua sendo o AWS Secrets Manager. Existe também um
fallback temporário, claramente delimitado, para permitir o deploy manual antes
da criação dos recursos de infraestrutura. Esse bloco deve ser removido assim
que ``ARN_SECRET`` estiver disponível em todos os ambientes.
"""

import json
import logging
import os

import boto3
from botocore.config import Config

from src.client.sts import StsException

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()  # pragma: no cover
except (ImportError, ModuleNotFoundError):  # fallback apenas para execução isolada deste patch
    logger = logging.getLogger(__name__)  # pragma: no cover


_BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)


class AwsSecretManagerConfig:
    """Carrega ``CLIENT_ID`` e ``CLIENT_SECRET`` do Secrets Manager."""

    def __init__(self, secret_arn: str, region: str | None = None):
        self.secret_arn = secret_arn
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

    def set_env_from_secret(self) -> None:
        """Mantém o comportamento original: sem ARN, não executa I/O."""
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


# ---------------------------------------------------------------------------
# TEMPORÁRIO — REMOVER ESTE BLOCO INTEIRO ASSIM QUE O SECRETS MANAGER EXISTIR.
# ---------------------------------------------------------------------------
# O ciphertext pode ficar hardcoded temporariamente. A chave de descriptografia
# NÃO deve ser commitada junto com ele. Para o deploy manual, prefira informar
# TEMP_CREDENTIALS_KEY como environment variable da própria Lambda.
#
# Se chave + ciphertext forem colocados no mesmo código, isso é apenas
# ofuscação, não proteção criptográfica. O repositório público mantém somente
# placeholders para impedir exposição acidental de credenciais reais.
_TEMP_ENCRYPTED_CREDENTIALS = {
    "dev": {
        "client_id": "<TEMP_ENCRYPTED_CLIENT_ID_DEV>",
        "client_secret": "<TEMP_ENCRYPTED_CLIENT_SECRET_DEV>",
    },
    "hml": {
        "client_id": "<TEMP_ENCRYPTED_CLIENT_ID_HML>",
        "client_secret": "<TEMP_ENCRYPTED_CLIENT_SECRET_HML>",
    },
    "prod": {
        "client_id": "<TEMP_ENCRYPTED_CLIENT_ID_PROD>",
        "client_secret": "<TEMP_ENCRYPTED_CLIENT_SECRET_PROD>",
    },
}
_TEMP_LOCAL_KEY = "<TEMP_FERNET_KEY_LOCAL_ONLY>"
_TEMP_SUPPORTED_ENVS = frozenset(_TEMP_ENCRYPTED_CREDENTIALS)


def _temporary_environment() -> str:
    """Resolve DEV/HML/PROD apenas para o fallback temporário."""
    value = (os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or "").strip().lower()
    if value not in _TEMP_SUPPORTED_ENVS:
        raise StsException("APP_ENV/ENVIRONMENT deve ser dev, hml ou prod no fallback temporário")
    return value


def _temporary_key() -> bytes:
    """Obtém a chave Fernet sem obrigar infraestrutura adicional."""
    value = (os.environ.get("TEMP_CREDENTIALS_KEY") or _TEMP_LOCAL_KEY).strip()
    if not value or value.startswith("<TEMP_"):
        raise StsException(
            "TEMP_CREDENTIALS_KEY não configurada; defina-a manualmente na Lambda "
            "ou substitua o placeholder somente na cópia local de deploy"
        )
    return value.encode("utf-8")


def _temporary_ciphertext(app_env: str, field: str) -> str:
    """Retorna o ciphertext configurado para o ambiente/campo solicitado."""
    value = _TEMP_ENCRYPTED_CREDENTIALS[app_env][field]
    if not value or value.startswith("<TEMP_ENCRYPTED_"):
        raise StsException(f"Ciphertext temporário não configurado para {field} em {app_env}")
    return value


def set_env_from_temporary_encrypted_credentials() -> None:
    """Descriptografa credenciais temporárias uma única vez no cold start.

    A função só deve ser chamada quando o caminho permanente de Secrets Manager
    não tiver populado ``CLIENT_ID``/``CLIENT_SECRET``. Os valores descriptografados
    são mantidos em ``os.environ`` e reaproveitados nas warm invocations.
    """
    if os.environ.get("CLIENT_ID") and os.environ.get("CLIENT_SECRET"):
        return

    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:
        raise StsException(
            "Dependência temporária 'cryptography' não está empacotada na Lambda"
        ) from exc

    app_env = _temporary_environment()
    try:
        fernet = Fernet(_temporary_key())
        client_id = fernet.decrypt(
            _temporary_ciphertext(app_env, "client_id").encode("utf-8")
        ).decode("utf-8")
        client_secret = fernet.decrypt(
            _temporary_ciphertext(app_env, "client_secret").encode("utf-8")
        ).decode("utf-8")
    except (ValueError, InvalidToken, UnicodeDecodeError) as exc:
        logger.error("Falha ao descriptografar credenciais temporárias: %s", type(exc).__name__)
        raise StsException("Credenciais temporárias inválidas") from exc

    if not client_id or not client_secret:
        raise StsException("Credenciais temporárias descriptografadas estão vazias")

    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret
    logger.warning(
        "Credenciais STS carregadas pelo fallback TEMPORÁRIO de código para ambiente %s",
        app_env,
    )
# ---------------------------------------------------------------------------
# FIM DO BLOCO TEMPORÁRIO.
# ---------------------------------------------------------------------------
