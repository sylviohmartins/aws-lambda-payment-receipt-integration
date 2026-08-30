import json
import os
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet

from src.client.sts import StsException
from src.config import credentials
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


def test_fallback_temporario_reaproveita_credenciais_ja_carregadas():
    env = {"CLIENT_ID": "already", "CLIENT_SECRET": "loaded"}
    with patch.dict(os.environ, env, clear=True), patch.object(
        credentials, "_temporary_environment"
    ) as resolver:
        credentials.set_env_from_temporary_encrypted_credentials()
    resolver.assert_not_called()


def test_fallback_temporario_descriptografa_por_ambiente():
    key = Fernet.generate_key()
    fernet = Fernet(key)
    encrypted = {
        "dev": {
            "client_id": fernet.encrypt(b"dev-client").decode(),
            "client_secret": fernet.encrypt(b"dev-secret").decode(),
        },
        "hml": {"client_id": "unused", "client_secret": "unused"},
        "prod": {"client_id": "unused", "client_secret": "unused"},
    }
    env = {"APP_ENV": "dev", "TEMP_CREDENTIALS_KEY": key.decode()}
    with patch.dict(os.environ, env, clear=True), patch.object(
        credentials, "_TEMP_ENCRYPTED_CREDENTIALS", encrypted
    ):
        credentials.set_env_from_temporary_encrypted_credentials()
        assert os.environ["CLIENT_ID"] == "dev-client"
        assert os.environ["CLIENT_SECRET"] == "dev-secret"


def test_fallback_temporario_aceita_environment_e_valida_ambiente():
    with patch.dict(os.environ, {"ENVIRONMENT": "HML"}, clear=True):
        assert credentials._temporary_environment() == "hml"
    with patch.dict(os.environ, {"APP_ENV": "qa"}, clear=True):
        with pytest.raises(StsException, match="dev, hml ou prod"):
            credentials._temporary_environment()


def test_fallback_temporario_exige_chave_e_ciphertexts_reais():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(StsException, match="TEMP_CREDENTIALS_KEY"):
            credentials._temporary_key()

    with patch.dict(os.environ, {"TEMP_CREDENTIALS_KEY": "abc"}, clear=True):
        assert credentials._temporary_key() == b"abc"
        with pytest.raises(StsException, match="Ciphertext temporário"):
            credentials._temporary_ciphertext("dev", "client_id")


def test_fallback_temporario_rejeita_token_criptografado_invalido():
    key = Fernet.generate_key().decode()
    encrypted = {
        "dev": {"client_id": "invalid", "client_secret": "invalid"},
        "hml": {"client_id": "unused", "client_secret": "unused"},
        "prod": {"client_id": "unused", "client_secret": "unused"},
    }
    env = {"APP_ENV": "dev", "TEMP_CREDENTIALS_KEY": key}
    with patch.dict(os.environ, env, clear=True), patch.object(
        credentials, "_TEMP_ENCRYPTED_CREDENTIALS", encrypted
    ):
        with pytest.raises(StsException, match="Credenciais temporárias inválidas"):
            credentials.set_env_from_temporary_encrypted_credentials()


def test_fallback_temporario_rejeita_credencial_vazia():
    key = Fernet.generate_key()
    fernet = Fernet(key)
    encrypted = {
        "dev": {
            "client_id": fernet.encrypt(b"").decode(),
            "client_secret": fernet.encrypt(b"secret").decode(),
        },
        "hml": {"client_id": "unused", "client_secret": "unused"},
        "prod": {"client_id": "unused", "client_secret": "unused"},
    }
    env = {"APP_ENV": "dev", "TEMP_CREDENTIALS_KEY": key.decode()}
    with patch.dict(os.environ, env, clear=True), patch.object(
        credentials, "_TEMP_ENCRYPTED_CREDENTIALS", encrypted
    ):
        with pytest.raises(StsException, match="estão vazias"):
            credentials.set_env_from_temporary_encrypted_credentials()


def test_fallback_temporario_informa_dependencia_ausente():
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "cryptography.fernet":
            raise ImportError("missing")
        return real_import(name, globals, locals, fromlist, level)

    with patch.dict(os.environ, {}, clear=True), patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(StsException, match="cryptography"):
            credentials.set_env_from_temporary_encrypted_credentials()
