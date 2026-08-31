"""Cliente HTTP para consulta autenticada de comprovantes de pagamento."""

import logging
import os
import time
import uuid

import requests

from src.client.http_retry import request_with_retries
from src.client.sts import gerar_token

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()  # pragma: no cover
except (ImportError, ModuleNotFoundError):
    logger = logging.getLogger(__name__)  # pragma: no cover


COMPROVANTES_PATH = "/comprovantes/v3/comprovantes"
HTTP_TIMEOUT_SECONDS = 10

# Configurações de ambiente com fallback temporário para PROD.
# No repositório público os valores permanecem como placeholders; na cópia de
# deploy, substitua-os pelos valores PROD enquanto a infraestrutura não os injeta.
DEFAULT_API_BASE_URL = "<API_BASE_URL_PROD>"
DEFAULT_X_APIGW_API_ID = "<X_APIGW_API_ID_PROD>"
DEFAULT_X_ITAU_FLOW_ID = "<X_ITAU_FLOW_ID_PROD>"


def _configured_value(env_name: str, default_value: str) -> str:
    """Obtém configuração por ambiente e bloqueia fallback não preenchido."""
    value = os.environ.get(env_name, default_value).strip()
    if not value or value.startswith("<"):
        raise ValueError(f"{env_name} não configurado")
    return value


def _base_url() -> str:
    """Obtém e normaliza a base URL da API."""
    return _configured_value("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def _headers(token: str) -> dict[str, str]:
    """Monta e valida os headers obrigatórios da API."""
    client_id = os.environ.get("CLIENT_ID", "")
    if not client_id:
        raise ValueError("CLIENT_ID não configurado para chamada da API")

    return {
        "Authorization": f"Bearer {token}",
        "x-apigw-api-id": _configured_value(
            "X_APIGW_API_ID", DEFAULT_X_APIGW_API_ID
        ),
        "x-itau-apikey-internal": client_id,
        "x-itau-apikey": client_id,
        "x-itau-flowID": _configured_value(
            "X_ITAU_FLOW_ID", DEFAULT_X_ITAU_FLOW_ID
        ),
        "x-itau-correlationID": str(uuid.uuid4()),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def consultar_comprovante(numero_comprovante: str) -> str:
    """Consulta comprovante e retorna somente o número de autenticação."""
    if not numero_comprovante:
        raise ValueError("numero_comprovante não informado")

    base_url = _base_url()
    token = gerar_token()
    endpoint = f"{base_url}{COMPROVANTES_PATH}/{numero_comprovante}"
    headers = _headers(token)

    logger.info(
        f"Iniciando consulta à API de comprovantes. "
        f"numero_comprovante={numero_comprovante}; url={endpoint}"
    )
    started = time.monotonic()

    try:
        response = request_with_retries(
            requests.get,
            operation="Consulta de comprovante",
            url=endpoint,
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            f"Consulta à API de comprovantes concluída. "
            f"numero_comprovante={numero_comprovante}; "
            f"http_status={response.status_code}; duration_ms={elapsed_ms}"
        )

    except requests.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        response_detail = None
        if exc.response is not None:
            try:
                response_detail = exc.response.text[:500]
            except Exception:  # pragma: no cover - proteção de observabilidade
                response_detail = None

        logger.error(
            f"Falha na API de comprovantes. "
            f"numero_comprovante={numero_comprovante}; "
            f"http_status={status_code}; exception={type(exc).__name__}; "
            f"detail={exc}; response={response_detail}",
            exc_info=True,
        )
        raise

    if not response.content:
        logger.error(
            f"API de comprovantes retornou resposta vazia. "
            f"numero_comprovante={numero_comprovante}; "
            f"http_status={response.status_code}"
        )
        raise ValueError("Resposta da API de comprovantes vazia")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.error(
            f"API de comprovantes retornou JSON inválido. "
            f"numero_comprovante={numero_comprovante}; "
            f"exception={type(exc).__name__}; detail={exc}",
            exc_info=True,
        )
        raise ValueError("Resposta inválida da API de comprovantes") from exc

    try:
        numero_autenticacao_comprovante = payload["data"]["identificacao"][
            "numero_autenticacao_comprovante"
        ]
    except (KeyError, TypeError) as exc:
        logger.error(
            f"Resposta da API de comprovantes sem numero_autenticacao_comprovante. "
            f"numero_comprovante={numero_comprovante}; "
            f"exception={type(exc).__name__}; detail={exc}",
            exc_info=True,
        )
        raise ValueError(
            "Campo data.identificacao.numero_autenticacao_comprovante "
            "não encontrado na resposta"
        ) from exc

    if not numero_autenticacao_comprovante:
        logger.error(
            f"API de comprovantes retornou numero_autenticacao_comprovante vazio. "
            f"numero_comprovante={numero_comprovante}"
        )
        raise ValueError("numero_autenticacao_comprovante retornado vazio")

    logger.info(
        f"Número de autenticação do comprovante obtido. "
        f"numero_comprovante={numero_comprovante}; "
        f"numero_autenticacao_comprovante={numero_autenticacao_comprovante}"
    )
    return numero_autenticacao_comprovante
