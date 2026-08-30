import logging
import os
import time

import requests

from src.client.http_retry import request_with_retries
from src.client.sts import gerar_token

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()
except (ImportError, ModuleNotFoundError):  # fallback apenas para execução isolada deste patch
    logger = logging.getLogger(__name__)


COMPROVANTES_PATH = "/comprovantes/v3/comprovantes"
HTTP_TIMEOUT_SECONDS = 10
BASE_URL_ENV = "BOLETOS_API_BASE_URL"

# TODO NOVA API COMPROVANTES: substituir pelos valores/configurações definitivos.
DEFAULT_X_APIGW_API_ID = "<FAKE_X_APIGW_API_ID>"
DEFAULT_X_ITAU_APIKEY_INTERNAL = "<FAKE_X_ITAU_APIKEY_INTERNAL>"
DEFAULT_X_ITAU_APIKEY = "<FAKE_X_ITAU_APIKEY>"
DEFAULT_FLOW_ID = "<FAKE_FLOW_ID>"
DEFAULT_CORRELATION_ID = "<FAKE_CORRELATION_ID>"


def _base_url() -> str:
    return os.environ.get(BASE_URL_ENV, "").rstrip("/")


def _headers(token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "x-apigw-api-id": os.environ.get("X_APIGW_API_ID", DEFAULT_X_APIGW_API_ID),
        "x-itau-apikey-internal": os.environ.get(
            "X_ITAU_APIKEY_INTERNAL",
            DEFAULT_X_ITAU_APIKEY_INTERNAL,
        ),
        "x-itau-apikey": os.environ.get("X_ITAU_APIKEY", DEFAULT_X_ITAU_APIKEY),
        "x-itau-flowID": os.environ.get("X_ITAU_FLOW_ID", DEFAULT_FLOW_ID),
        "x-itau-correlationID": os.environ.get(
            "X_ITAU_CORRELATION_ID",
            DEFAULT_CORRELATION_ID,
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    fake_headers = [
        name
        for name, value in headers.items()
        if name != "Authorization" and isinstance(value, str) and value.startswith("<FAKE_")
    ]
    if fake_headers:
        raise ValueError(
            "Headers da API de comprovantes ainda não configurados: " + ", ".join(fake_headers)
        )

    return headers


def consultar_comprovante(identificador_comprovante: str):
    """GET sem body para consultar comprovante usando token STS."""
    base_url = _base_url()
    if not base_url:
        raise ValueError(f"Base URL não configurada em {BASE_URL_ENV}")
    if not identificador_comprovante:
        raise ValueError("identificador_comprovante não informado")

    token = gerar_token()
    endpoint = f"{base_url}{COMPROVANTES_PATH}/{identificador_comprovante}"
    headers = _headers(token)

    logger.info("Iniciando consulta à API de comprovantes")
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
            "Consulta à API de comprovantes concluída com HTTP %s em %sms",
            response.status_code,
            elapsed_ms,
        )
    except requests.RequestException as exc:
        logger.error("Falha técnica na API de comprovantes: %s", type(exc).__name__)
        raise

    # TODO NOVA API COMPROVANTES:
    # interpretar o contrato real da response quando ele for fornecido.
    if not response.content:
        return {}
    return response.json()
