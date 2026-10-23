"""
Прокси-модуль обратной совместимости для common.
Глобальные синглтоны и контекст ядра вынесены в core/common.py.
"""
from core.common import *  # noqa: F401, F403
from core.common import (  # noqa: F401
    CONFIG,
    SYSTEM_METRICS,
    START_TIME,
    bot,
    db,
    dp,
    safe_send,
    safe_answer,
    setup_bot_commands,
    validate_config,
    update_env_variable,
    is_maintenance_active,
    set_maintenance_active,
    get_maintenance_info,
    broadcast_maintenance_alert,
    route_new_candidate_ticket,
    route_new_inquiry_ticket,
    grant_admin_and_sync,
    revoke_admin_and_sync,
)
