import json
import os
from unittest.mock import Mock, patch

from src.config.credentials import AwsSecretManagerConfig


def test_secret_manager_carrega_client_id_e_secret_com_retry_aws_configurado():
    client = Mock()
    client.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {"client_id": "fake-client", "client_secret": "fake-secret"}
        )
    }

    with patch.dict(os.environ, {}, clear=True), patch(
        "src.config.credentials.boto3.client",
        return_value=client,
    ) as boto_client:
        AwsSecretManagerConfig(
            "arn:aws:secretsmanager:sa-east-1:000:secret:fake",
            "sa-east-1",
        ).set_env_from_secret()

        assert os.environ["CLIENT_ID"] == "fake-client"
        assert os.environ["CLIENT_SECRET"] == "fake-secret"
        _, kwargs = boto_client.call_args
        assert kwargs["config"].retries["max_attempts"] == 3
