import json
import os

from src.client.comprovantes_api import consultar_comprovante
from src.config.credentials import (
    AwsSecretManagerConfig,
    set_env_from_temporary_encrypted_credentials,
)

from src.utils.logger_util import prepare_logger
from src.utils.retry_utils import execute_with_retries
from src.services.Dynamodb import Dynamodb

logger = prepare_logger()


def _bootstrap_sts():
    """Carrega credenciais STS no cold start, priorizando o caminho permanente."""
    arn_secret = os.environ.get("ARN_SECRET", "")
    AwsSecretManagerConfig(arn_secret).set_env_from_secret()

    # -----------------------------------------------------------------------
    # TEMPORÁRIO / PRODUÇÃO — REMOVER ESTE BLOCO quando o secret for criado.
    # O fallback não identifica ambiente: ele existe somente para o deploy
    # manual temporário em produção e é acionado apenas se o caminho permanente
    # não tiver carregado CLIENT_ID/CLIENT_SECRET.
    # -----------------------------------------------------------------------
    if not os.environ.get("CLIENT_ID") or not os.environ.get("CLIENT_SECRET"):
        set_env_from_temporary_encrypted_credentials()
    # -------------------------- FIM TEMPORÁRIO ------------------------------


_bootstrap_sts()


def handler(event, _context):
    batch_item_failures = []
    for record in event["Records"]:
        try:
            message = json.loads(record["body"])

            # comentários existentes do fluxo original permanecem intactos
            identificador_comprovante = message.get("numero_autenticacao_comprovante")
            if identificador_comprovante:
                comprovante = consultar_comprovante(identificador_comprovante)

                # TODO NOVA API COMPROVANTES:
                # utilizar `comprovante` quando o contrato real da response for definido.

            execute_with_retries(
                Dynamodb().update_item,
                message,
            )
        except Exception:
            batch_item_failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": batch_item_failures}
