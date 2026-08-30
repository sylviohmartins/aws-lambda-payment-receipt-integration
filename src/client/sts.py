import logging
import os

import requests
from requests.auth import HTTPBasicAuth

from src.client.http_retry import request_with_retries

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()
except (ImportError, ModuleNotFoundError):  # fallback apenas para execução isolada deste patch
    logger = logging.getLogger(__name__)


TOKEN_TIMEOUT_SECONDS = 10


class StsException(Exception):
    """Falha na configuração ou obtenção do token STS."""


def gerar_token() -> str:
    """Obtém token pelo fluxo client_credentials usado pela Lambda de cancelamentos."""
    token_url = os.environ.get("TOKEN_URL", "")
    client_id = os.environ.get("CLIENT_ID", "")
    client_secret = os.environ.get("CLIENT_SECRET", "")

    if not token_url or not client_id or not client_secret:
        raise StsException("Configuração de autenticação incompleta")

    logger.info("Iniciando obtenção de token STS")
    try:
        response = request_with_retries(
            requests.post,
            operation="STS",
            url=token_url,
            auth=HTTPBasicAuth(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=TOKEN_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise StsException("Token não retornado pelo STS")

        logger.info("Token STS obtido com sucesso")
        return token
    except StsException:
        raise
    except requests.RequestException as exc:
        logger.error("Falha ao obter token STS: %s", type(exc).__name__)
        raise StsException("Erro ao gerar token") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        logger.error("Resposta inválida do STS: %s", type(exc).__name__)
        raise StsException("Resposta inválida do STS") from exc
