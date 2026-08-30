import json

from src.utils.logger_util import prepare_logger
from src.utils.retry_utils import execute_with_retries
from src.services.Dynamodb import Dynamodb

logger = prepare_logger()


def handler(event, _context):
    batch_item_failures = []
    for record in event["Records"]:
        try:
            message = json.loads(record["body"])

            # comentários existentes do fluxo original permanecem intactos
            execute_with_retries(
                Dynamodb().update_item,
                message,
            )
        except Exception:
            batch_item_failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": batch_item_failures}
