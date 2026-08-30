import os
from unittest.mock import Mock, patch

import requests

from src.client.sts import StsException, gerar_token


def test_gerar_token_usa_client_credentials_basic_auth_e_logica_de_retry():
    env = {
        "TOKEN_URL": "https://sts.fake/token",
        "CLIENT_ID": "fake-client",
        "CLIENT_SECRET": "fake-secret",
    }
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"access_token": "fake-token"}

    with patch.dict(os.environ, env, clear=False), patch(
        "src.client.sts.requests.post",
        return_value=response,
    ) as post:
        token = gerar_token()

    assert token == "fake-token"
    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["data"] == {"grant_type": "client_credentials"}
    assert kwargs["timeout"] == 10
    assert kwargs["auth"].username == "fake-client"
    assert kwargs["auth"].password == "fake-secret"


def test_sts_repete_timeout_transitorio_e_recupera():
    env = {
        "TOKEN_URL": "https://sts.fake/token",
        "CLIENT_ID": "fake-client",
        "CLIENT_SECRET": "fake-secret",
    }
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"access_token": "fake-token"}

    with patch.dict(os.environ, env, clear=False), patch(
        "src.client.sts.requests.post",
        side_effect=[requests.Timeout("timeout"), response],
    ) as post, patch("src.client.http_retry.time.sleep"):
        assert gerar_token() == "fake-token"

    assert post.call_count == 2


def test_erro_sts_apos_tentativas_vira_sts_exception():
    env = {
        "TOKEN_URL": "https://sts.fake/token",
        "CLIENT_ID": "fake-client",
        "CLIENT_SECRET": "fake-secret",
    }
    with patch.dict(os.environ, env, clear=False), patch(
        "src.client.sts.requests.post",
        side_effect=requests.Timeout("timeout"),
    ) as post, patch("src.client.http_retry.time.sleep"):
        try:
            gerar_token()
        except StsException as exc:
            assert str(exc) == "Erro ao gerar token"
        else:
            raise AssertionError("Era esperado StsException")

    assert post.call_count == 3
