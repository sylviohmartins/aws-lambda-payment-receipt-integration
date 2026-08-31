"""Cliente STS/OAuth para obtenção de access token via client_credentials."""

import logging
import os

import requests
from requests.auth import HTTPBasicAuth

from src.client.http_retry import request_with_retries

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()  # pragma: no cover
except (ImportError, ModuleNotFoundError):
    logger = logging.getLogger(__name__)  # pragma: no cover


TOKEN_TIMEOUT_SECONDS = 10

# Configuração de ambiente com fallback temporário para PROD.
# No repositório público o valor permanece como placeholder; na cópia de deploy,
# substitua-o pelo endpoint PROD enquanto TOKEN_URL ainda não vier da infraestrutura.
DEFAULT_TOKEN_URL = "<TOKEN_URL_PROD>"


class StsException(Exception):
    """Falha na configuração ou obtenção do token STS."""


def _token_url() -> str:
    """Obtém a URL do STS por variável de ambiente ou fallback de PROD."""
    token_url = os.environ.get("TOKEN_URL", DEFAULT_TOKEN_URL).strip()
    if not token_url or token_url.startswith("<"):
        raise StsException("TOKEN_URL não configurada")
    return token_url


def gerar_token() -> str:
    """Obtém token STS usando OAuth client_credentials."""
    token_url = _token_url()
    client_id = os.environ.get("CLIENT_ID", "")
    client_secret = os.environ.get("CLIENT_SECRET", "")

    if not client_id:
        raise StsException("CLIENT_ID não configurado")
    if not client_secret:
        raise StsException("CLIENT_SECRET não configurado")

    logger.info(f"Iniciando obtenção de token STS. url={token_url}")

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

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise StsException("Campo access_token não retornado pelo STS")

        logger.info(
            f"Token STS obtido com sucesso. http_status={response.status_code}"
        )
        return token

    except StsException:
        raise
    except requests.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.error(
            f"Falha ao obter token STS. http_status={status_code}; "
            f"exception={type(exc).__name__}; detail={exc}",
            exc_info=True,
        )
        raise StsException(
            f"Erro ao gerar token STS: {type(exc).__name__}: {exc}"
        ) from exc
    except (TypeError, ValueError, AttributeError) as exc:
        logger.error(
            f"Resposta inválida do STS. exception={type(exc).__name__}; detail={exc}",
            exc_info=True,
        )
        raise StsException(
            f"Resposta inválida do STS: {type(exc).__name__}: {exc}"
        ) from exc
