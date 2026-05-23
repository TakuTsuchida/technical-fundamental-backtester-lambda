from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    mail_param = os.environ["MAIL_PARAM"]
    password_param = os.environ["PASSWORD_PARAM"]
    token_param = os.environ["TOKEN_PARAM"]
    logger.info(
        "token-refresher invoked",
        extra={
            "mail_param": mail_param,
            "password_param": password_param,
            "token_param": token_param,
        },
    )
    raise NotImplementedError("token-refresher is not yet implemented")
