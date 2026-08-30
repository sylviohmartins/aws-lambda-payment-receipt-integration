import os
from unittest.mock import Mock, patch

import requests

from src.client.comprovantes_api import consultar_comprovante


def _env():
    return {
        "BOLETOS_API_BASE_URL": "https://api.fake.itau",
        "X_APIGW_API_ID": "fake-apigw",
        "X_ITAU_APIKEY_INTERNAL": "fake-internal",
        "X_ITAU_APIKEY": "fake-key",
        "X_ITAU_FLOW_ID": "fake-flow-id",
        "X_ITAU_CORRELATION_ID": "fake-correlation-id",
    }


def _success_response(payload=None):
    response = Mock(status_code=200)
    response.content = b"{}"
    response.raise_for_status.return_value = None
    response.json.return_value = payload or {"ok": True}
    return response


def test_consulta_comprovante_get_sem_body_com_headers_e_bearer():
    response = _success_response()

    with patch.dict(os.environ, _env(), clear=False), patch(
        "src.client.comprovantes_api.gerar_token",
        return_value="fake-token",
    ) as token, patch(
        "src.client.comprovantes_api.requests.get",
        return_value=response,
    ) as get:
        result = consultar_comprovante("COMP-123")

    token.assert_called_once_with()
    get.assert_called_once()
    _, kwargs = get.call_args
    assert kwargs["url"] == "https://api.fake.itau/comprovantes/v3/comprovantes/COMP-123"
    assert "json" not in kwargs
    assert "data" not in kwargs
    assert kwargs["timeout"] == 10
    assert kwargs["headers"] == {
        "Authorization": "Bearer fake-token",
        "x-apigw-api-id": "fake-apigw",
        "x-itau-apikey-internal": "fake-internal",
        "x-itau-apikey": "fake-key",
        "x-itau-flowID": "fake-flow-id",
        "x-itau-correlationID": "fake-correlation-id",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    assert result == {"ok": True}


def test_get_repete_503_e_recupera():
    retryable = Mock(status_code=503)
    success = _success_response()

    with patch.dict(os.environ, _env(), clear=False), patch(
        "src.client.comprovantes_api.gerar_token",
        return_value="fake-token",
    ), patch(
        "src.client.comprovantes_api.requests.get",
        side_effect=[retryable, success],
    ) as get, patch("src.client.http_retry.time.sleep"):
        assert consultar_comprovante("COMP-123") == {"ok": True}

    assert get.call_count == 2


def test_get_nao_repete_400():
    response = Mock(status_code=400)
    response.raise_for_status.side_effect = requests.HTTPError("bad request")

    with patch.dict(os.environ, _env(), clear=False), patch(
        "src.client.comprovantes_api.gerar_token",
        return_value="fake-token",
    ), patch(
        "src.client.comprovantes_api.requests.get",
        return_value=response,
    ) as get:
        try:
            consultar_comprovante("COMP-123")
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("Era esperado HTTPError")

    assert get.call_count == 1


def test_resposta_sem_conteudo_retorna_objeto_vazio():
    response = Mock(status_code=204)
    response.content = b""
    response.raise_for_status.return_value = None

    with patch.dict(os.environ, _env(), clear=False), patch(
        "src.client.comprovantes_api.gerar_token",
        return_value="fake-token",
    ), patch(
        "src.client.comprovantes_api.requests.get",
        return_value=response,
    ):
        assert consultar_comprovante("COMP-204") == {}


def test_placeholders_nao_podem_ser_enviados_acidentalmente():
    env = {"BOLETOS_API_BASE_URL": "https://api.fake.itau"}

    with patch.dict(os.environ, env, clear=True), patch(
        "src.client.comprovantes_api.gerar_token",
        return_value="fake-token",
    ), patch("src.client.comprovantes_api.requests.get") as get:
        try:
            consultar_comprovante("COMP-123")
        except ValueError as exc:
            assert "ainda não configurados" in str(exc)
        else:
            raise AssertionError("Era esperado bloqueio dos placeholders")

    get.assert_not_called()
