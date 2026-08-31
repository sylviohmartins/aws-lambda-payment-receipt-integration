from unittest.mock import Mock, patch

import pytest
import requests

from src.client.http_retry import _backoff, request_with_retries


def test_backoff_exponencial_com_jitter():
    with patch("src.client.http_retry.random.uniform", return_value=0.2):
        assert _backoff(1) == 0.7
        assert _backoff(2) == 1.2


def test_retorna_sem_retry_em_status_nao_transitorio():
    response = Mock(status_code=400)
    fn = Mock(return_value=response)
    assert request_with_retries(fn, operation="op") is response
    fn.assert_called_once()


def test_repete_status_transitorio_e_recupera():
    fn = Mock(side_effect=[Mock(status_code=503), Mock(status_code=200)])
    with patch("src.client.http_retry._backoff", return_value=0), patch(
        "src.client.http_retry.time.sleep"
    ) as sleep:
        assert request_with_retries(fn, operation="op").status_code == 200
    assert fn.call_count == 2
    sleep.assert_called_once_with(0)


def test_retorna_ultimo_status_transitorio_ao_esgotar_tentativas():
    response = Mock(status_code=503)
    fn = Mock(return_value=response)
    with patch("src.client.http_retry._backoff", return_value=0), patch(
        "src.client.http_retry.time.sleep"
    ):
        assert request_with_retries(fn, operation="op", max_attempts=2) is response
    assert fn.call_count == 2


def test_repete_timeout_e_recupera_com_log_interpolado():
    response = Mock(status_code=200)
    fn = Mock(side_effect=[requests.Timeout("timeout detalhado"), response])
    with patch("src.client.http_retry._backoff", return_value=0), patch(
        "src.client.http_retry.time.sleep"
    ), patch("src.client.http_retry.logger.warning") as warning:
        assert request_with_retries(fn, operation="op") is response

    assert fn.call_count == 2
    message = warning.call_args.args[0]
    assert "Timeout" in message
    assert "timeout detalhado" in message
    assert "%s" not in message


def test_propaga_timeout_apos_esgotar_tentativas_com_log_detalhado():
    fn = Mock(side_effect=requests.Timeout("timeout final"))
    with patch("src.client.http_retry._backoff", return_value=0), patch(
        "src.client.http_retry.time.sleep"
    ), patch("src.client.http_retry.logger.error") as error:
        with pytest.raises(requests.Timeout):
            request_with_retries(fn, operation="op", max_attempts=2)

    assert fn.call_count == 2
    message = error.call_args.args[0]
    assert "Timeout" in message
    assert "timeout final" in message
    assert "%s" not in message
    assert error.call_args.kwargs["exc_info"] is True


def test_zero_tentativas_expoe_fluxo_invalido():
    with pytest.raises(RuntimeError, match="Fluxo de retry inválido"):
        request_with_retries(Mock(), operation="op", max_attempts=0)
