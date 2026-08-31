import os
from unittest.mock import Mock, patch

import pytest
import requests

from src.client.comprovantes_api import _base_url, consultar_comprovante


def _env():
    return {
        "API_BASE_URL": "https://api.fake.itau/",
        "CLIENT_ID": "fake-client",
        "X_APIGW_API_ID": "fake-apigw",
        "X_ITAU_FLOW_ID": "fake-flow-id",
    }


def _success_response(numero_autenticacao="AUTH-123"):
    response = Mock(status_code=200)
    response.content = b'{"data":{}}'
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": {
            "identificacao": {
                "numero_autenticacao_comprovante": numero_autenticacao
            }
        }
    }
    return response


def test_base_url_normaliza_barra_final():
    with patch.dict(os.environ, _env(), clear=True):
        assert _base_url() == "https://api.fake.itau"


def test_consulta_get_sem_body_reutiliza_client_id_e_retorna_numero_autenticacao():
    response = _success_response()

    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ) as token, patch(
        "src.client.comprovantes_api.requests.get", return_value=response
    ) as get, patch(
        "src.client.comprovantes_api.uuid.uuid4", return_value="uuid-123"
    ):
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
        "x-itau-apikey-internal": "fake-client",
        "x-itau-apikey": "fake-client",
        "x-itau-flowID": "fake-flow-id",
        "x-itau-correlationID": "uuid-123",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    assert result == "AUTH-123"


def test_get_repete_503_e_recupera():
    retryable = Mock(status_code=503)
    success = _success_response()

    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch(
        "src.client.comprovantes_api.requests.get",
        side_effect=[retryable, success],
    ) as get, patch("src.client.http_retry.time.sleep"):
        assert consultar_comprovante("COMP-123") == "AUTH-123"

    assert get.call_count == 2


def test_get_nao_repete_400_e_propaga_detalhe():
    response = Mock(status_code=400)
    response.text = '{"errors":["bad request"]}'
    error = requests.HTTPError("400 Client Error")
    error.response = response
    response.raise_for_status.side_effect = error

    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch(
        "src.client.comprovantes_api.requests.get", return_value=response
    ) as get:
        with pytest.raises(requests.HTTPError, match="400 Client Error"):
            consultar_comprovante("COMP-123")

    assert get.call_count == 1


def test_resposta_sem_conteudo_falha():
    response = Mock(status_code=204)
    response.content = b""
    response.raise_for_status.return_value = None

    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch(
        "src.client.comprovantes_api.requests.get", return_value=response
    ):
        with pytest.raises(ValueError, match="Resposta da API de comprovantes vazia"):
            consultar_comprovante("COMP-204")


def test_json_invalido_falha():
    response = _success_response()
    response.json.side_effect = ValueError("invalid json")

    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch(
        "src.client.comprovantes_api.requests.get", return_value=response
    ):
        with pytest.raises(ValueError, match="Resposta inválida"):
            consultar_comprovante("COMP-123")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": {"identificacao": {}}},
    ],
)
def test_campo_numero_autenticacao_ausente_falha(payload):
    response = _success_response()
    response.json.return_value = payload

    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch(
        "src.client.comprovantes_api.requests.get", return_value=response
    ):
        with pytest.raises(ValueError, match="numero_autenticacao_comprovante"):
            consultar_comprovante("COMP-123")


def test_numero_autenticacao_vazio_falha():
    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch(
        "src.client.comprovantes_api.requests.get",
        return_value=_success_response(""),
    ):
        with pytest.raises(ValueError, match="retornado vazio"):
            consultar_comprovante("COMP-123")


@pytest.mark.parametrize(
    "missing",
    ["API_BASE_URL", "X_APIGW_API_ID", "X_ITAU_FLOW_ID"],
)
def test_configuracao_de_ambiente_ausente_com_placeholder_falha(missing):
    env = _env()
    env.pop(missing)

    with patch.dict(os.environ, env, clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch("src.client.comprovantes_api.requests.get") as get:
        with pytest.raises(ValueError, match=f"{missing} não configurado"):
            consultar_comprovante("COMP-123")

    get.assert_not_called()


def test_client_id_e_numero_comprovante_sao_obrigatorios():
    env = _env()
    env.pop("CLIENT_ID")

    with patch.dict(os.environ, env, clear=True), patch(
        "src.client.comprovantes_api.gerar_token", return_value="fake-token"
    ), patch("src.client.comprovantes_api.requests.get") as get:
        with pytest.raises(ValueError, match="CLIENT_ID"):
            consultar_comprovante("COMP-123")
    get.assert_not_called()

    with patch.dict(os.environ, _env(), clear=True):
        with pytest.raises(ValueError, match="numero_comprovante"):
            consultar_comprovante("")
