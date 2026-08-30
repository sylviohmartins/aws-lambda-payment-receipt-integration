"""Carregamento de credenciais STS.

O caminho permanente continua sendo o AWS Secrets Manager. Existe também um
fallback de código, temporário e restrito ao deploy manual em produção, para o
intervalo anterior ao provisionamento da infraestrutura definitiva.
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
        """Mantém o comportamento permanente: sem ARN, não executa I/O."""
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
# TEMPORÁRIO / PRODUÇÃO — REMOVER ESTE BLOCO INTEIRO QUANDO O SECRET EXISTIR.
# ---------------------------------------------------------------------------
# Este fallback existe exclusivamente para o deploy manual temporário em PROD.
# Não há detecção de DEV/HML/PROD: os dois ciphertexts abaixo são os de produção.
#
# Passos para remoção futura:
#   1. excluir deste marcador até "FIM DO BLOCO TEMPORÁRIO";
#   2. remover o import/chamada de set_env_from_temporary_encrypted_credentials
#      do bootstrap em lambda_function.py;
#   3. remover requirements-temporary.txt / cryptography do pacote.
#
# O ciphertext pode ficar hardcoded somente na cópia local do deploy. A chave
# deve preferencialmente ser informada em TEMP_CREDENTIALS_KEY. Se chave e
# ciphertext forem colocados juntos no fonte, o mecanismo é apenas ofuscação.
# Nunca commite credenciais, ciphertexts reais ou a chave neste repositório.
_TEMP_ENCRYPTED_CLIENT_ID = "<TEMP_ENCRYPTED_CLIENT_ID_PROD>"
_TEMP_ENCRYPTED_CLIENT_SECRET = "<TEMP_ENCRYPTED_CLIENT_SECRET_PROD>"
_TEMP_LOCAL_KEY = "<TEMP_FERNET_KEY_LOCAL_ONLY>"


def set_env_from_temporary_encrypted_credentials() -> None:  # pragma: no cover
    """Descriptografa o fallback temporário de PROD durante o cold start.

    Não há seleção de ambiente. A função só é chamada quando o caminho
    permanente não carregou ``CLIENT_ID``/``CLIENT_SECRET``. Após a primeira
    execução, os valores permanecem no processo e são reutilizados nas warm
    invocations.

    Este método é deliberadamente excluído da cobertura por ser código
    transitório que será removido assim que o Secrets Manager for provisionado.
    """
    if os.environ.get("CLIENT_ID") and os.environ.get("CLIENT_SECRET"):
        return

    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:
        raise StsException(
            "Dependência temporária 'cryptography' não está empacotada na Lambda"
        ) from exc

    key = (os.environ.get("TEMP_CREDENTIALS_KEY") or _TEMP_LOCAL_KEY).strip()
    if not key or key.startswith("<TEMP_"):
        raise StsException(
            "TEMP_CREDENTIALS_KEY não configurada; informe a chave manualmente "
            "ou substitua o placeholder somente na cópia local do deploy"
        )

    if (
        not _TEMP_ENCRYPTED_CLIENT_ID
        or _TEMP_ENCRYPTED_CLIENT_ID.startswith("<TEMP_ENCRYPTED_")
        or not _TEMP_ENCRYPTED_CLIENT_SECRET
        or _TEMP_ENCRYPTED_CLIENT_SECRET.startswith("<TEMP_ENCRYPTED_")
    ):
        raise StsException("Ciphertexts temporários de produção não configurados")

    try:
        fernet = Fernet(key.encode("utf-8"))
        client_id = fernet.decrypt(_TEMP_ENCRYPTED_CLIENT_ID.encode("utf-8")).decode("utf-8")
        client_secret = fernet.decrypt(_TEMP_ENCRYPTED_CLIENT_SECRET.encode("utf-8")).decode("utf-8")
    except (ValueError, InvalidToken, UnicodeDecodeError) as exc:
        logger.error("Falha ao descriptografar credenciais temporárias: %s", type(exc).__name__)
        raise StsException("Credenciais temporárias inválidas") from exc

    if not client_id or not client_secret:
        raise StsException("Credenciais temporárias descriptografadas estão vazias")

    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret
    logger.warning("Credenciais STS carregadas pelo fallback TEMPORÁRIO de produção")
# ---------------------------------------------------------------------------
# FIM DO BLOCO TEMPORÁRIO.
# ---------------------------------------------------------------------------
