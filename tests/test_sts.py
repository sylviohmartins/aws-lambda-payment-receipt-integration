import os
from unittest.mock import Mock, patch

import pytest
import requests

from src.client.sts import StsException, gerar_token


def _env():
    return {
        "TOKEN_URL": "https://sts.fake/token",
        "CLIENT_ID": "fake-client",
        "CLIENT_SECRET": "fake-secret",
    }


def _response(payload=None):
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = payload if payload is not None else {"access_token": "fake-token"}
    return response


def test_gerar_token_usa_client_credentials_basic_auth():
    response = _response()
    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.sts.requests.post", return_value=response
    ) as post:
        assert gerar_token() == "fake-token"
    _, kwargs = post.call_args
    assert kwargs["data"] == {"grant_type": "client_credentials"}
    assert kwargs["timeout"] == 10
    assert kwargs["auth"].username == "fake-client"
    assert kwargs["auth"].password == "fake-secret"


def test_configuracao_incompleta_falha_antes_da_rede():
    with patch.dict(os.environ, {}, clear=True), patch("src.client.sts.requests.post") as post:
        with pytest.raises(StsException, match="Configuração de autenticação incompleta"):
            gerar_token()
    post.assert_not_called()


def test_sts_repete_timeout_transitorio_e_recupera():
    response = _response()
    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.sts.requests.post", side_effect=[requests.Timeout("timeout"), response]
    ) as post, patch("src.client.http_retry.time.sleep"):
        assert gerar_token() == "fake-token"
    assert post.call_count == 2


def test_erro_sts_apos_tentativas_vira_sts_exception():
    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.sts.requests.post", side_effect=requests.Timeout("timeout")
    ) as post, patch("src.client.http_retry.time.sleep"):
        with pytest.raises(StsException, match="Erro ao gerar token"):
            gerar_token()
    assert post.call_count == 3


def test_sts_sem_access_token_vira_sts_exception():
    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.sts.requests.post", return_value=_response({})
    ):
        with pytest.raises(StsException, match="Token não retornado"):
            gerar_token()


def test_resposta_json_invalida_vira_sts_exception():
    response = _response()
    response.json.side_effect = ValueError("invalid json")
    with patch.dict(os.environ, _env(), clear=True), patch(
        "src.client.sts.requests.post", return_value=response
    ):
        with pytest.raises(StsException, match="Resposta inválida"):
            gerar_token()
