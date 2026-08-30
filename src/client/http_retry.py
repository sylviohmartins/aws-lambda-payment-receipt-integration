import logging
import random
import time
from collections.abc import Callable

import requests

try:
    from src.utils.logger_util import prepare_logger

    logger = prepare_logger()
except (ImportError, ModuleNotFoundError):  # fallback apenas para execução isolada deste patch
    logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (requests.Timeout, requests.ConnectionError)


def _backoff(attempt: int) -> float:
    return BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.25)


def request_with_retries(
    request_fn: Callable[..., requests.Response],
    *,
    operation: str,
    max_attempts: int = MAX_ATTEMPTS,
    **request_kwargs,
) -> requests.Response:
    """Executa uma chamada HTTP com retry apenas para falhas transitórias."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = request_fn(**request_kwargs)
        except RETRYABLE_EXCEPTIONS as exc:
            if attempt >= max_attempts:
                logger.error(
                    "%s falhou após %s tentativas: %s",
                    operation,
                    max_attempts,
                    type(exc).__name__,
                )
                raise

            wait_seconds = _backoff(attempt)
            logger.warning(
                "%s falhou com %s; nova tentativa %s/%s em %.2fs",
                operation,
                type(exc).__name__,
                attempt + 1,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= max_attempts:
            return response

        wait_seconds = _backoff(attempt)
        logger.warning(
            "%s retornou HTTP %s; nova tentativa %s/%s em %.2fs",
            operation,
            response.status_code,
            attempt + 1,
            max_attempts,
            wait_seconds,
        )
        time.sleep(wait_seconds)

    raise RuntimeError(f"Fluxo de retry inválido para {operation}")
