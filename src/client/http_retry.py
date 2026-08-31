"""Política explícita de retry para chamadas HTTP com falhas transitórias."""

import logging
import random
import time
from collections.abc import Callable

import requests

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()  # pragma: no cover
except (ImportError, ModuleNotFoundError):
    logger = logging.getLogger(__name__)  # pragma: no cover


MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (requests.Timeout, requests.ConnectionError)


def _backoff(attempt: int) -> float:
    """Calcula exponential backoff com pequeno jitter."""
    return BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.25)


def request_with_retries(
    request_fn: Callable[..., requests.Response],
    *,
    operation: str,
    max_attempts: int = MAX_ATTEMPTS,
    **request_kwargs,
) -> requests.Response:
    """Executa chamada HTTP com retry apenas para falhas transitórias."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = request_fn(**request_kwargs)
        except RETRYABLE_EXCEPTIONS as exc:
            exception_name = type(exc).__name__
            exception_message = str(exc)

            if attempt >= max_attempts:
                logger.error(
                    f"{operation} falhou após {max_attempts} tentativas. "
                    f"exception={exception_name}; detail={exception_message}",
                    exc_info=True,
                )
                raise

            wait_seconds = _backoff(attempt)
            logger.warning(
                f"{operation} falhou na tentativa {attempt}/{max_attempts}. "
                f"exception={exception_name}; detail={exception_message}; "
                f"retry={attempt + 1}/{max_attempts}; wait={wait_seconds:.2f}s"
            )
            time.sleep(wait_seconds)
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES:
            return response

        if attempt >= max_attempts:
            logger.error(
                f"{operation} permaneceu com erro após {max_attempts} tentativas. "
                f"http_status={response.status_code}"
            )
            return response

        wait_seconds = _backoff(attempt)
        logger.warning(
            f"{operation} retornou erro transitório. "
            f"http_status={response.status_code}; attempt={attempt}/{max_attempts}; "
            f"retry={attempt + 1}/{max_attempts}; wait={wait_seconds:.2f}s"
        )
        time.sleep(wait_seconds)

    raise RuntimeError(f"Fluxo de retry inválido para operation={operation}")
