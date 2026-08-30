import json
import os
from unittest.mock import Mock, patch

import pytest

from src.client.sts import StsException
from src.config.credentials import AwsSecretManagerConfig


def test_secret_manager_sem_arn_preserva_comportamento_original_e_nao_chama_aws():
    with patch.dict(os.environ, {}, clear=True), patch(
        "src.config.credentials.boto3.client"
    ) as boto_client:
        AwsSecretManagerConfig("").set_env_from_secret()

    boto_client.assert_not_called()


def test_secret_manager_carrega_client_id_e_secret_com_retry_aws_configurado():
    client = Mock()
    client.get_secret_value.return_value = {
        "SecretString": json.dumps({"client_id": "fake-client", "client_secret": "fake-secret"})
    }

    with patch.dict(os.environ, {}, clear=True), patch(
        "src.config.credentials.boto3.client", return_value=client
    ) as boto_client:
        AwsSecretManagerConfig(
            "arn:aws:secretsmanager:sa-east-1:000:secret:fake",
            "sa-east-1",
        ).set_env_from_secret()

        assert os.environ["CLIENT_ID"] == "fake-client"
        assert os.environ["CLIENT_SECRET"] == "fake-secret"
        _, kwargs = boto_client.call_args
        assert kwargs["config"].retries["max_attempts"] == 3


def test_secret_manager_rejeita_secret_sem_credenciais():
    client = Mock()
    client.get_secret_value.return_value = {"SecretString": "{}"}
    with patch.dict(os.environ, {}, clear=True), patch(
        "src.config.credentials.boto3.client", return_value=client
    ):
        with pytest.raises(StsException, match="Credenciais ausentes"):
            AwsSecretManagerConfig("arn:fake", "sa-east-1").set_env_from_secret()


def test_secret_manager_encapsula_erro_aws():
    with patch.dict(os.environ, {}, clear=True), patch(
        "src.config.credentials.boto3.client", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(StsException, match="Erro ao carregar credenciais"):
            AwsSecretManagerConfig("arn:fake", "sa-east-1").set_env_from_secret()
