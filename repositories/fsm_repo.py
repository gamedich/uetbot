import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("UET_HR_BOT")


class FSMMixin:
    """Миксин хранения состояний FSM и персистентных сессий внешних мессенджеров (VK / MAX / TG)."""

    def set_fsm_state(self, key: str, state: Optional[str]) -> None:
        """Сохранение состояния FSM в постоянное хранилище."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            if state is None:
                cursor.execute(
                    "UPDATE fsm_storage SET state = NULL, updated_at = CURRENT_TIMESTAMP WHERE storage_key = ?",
                    (key,),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO fsm_storage (storage_key, state, data, updated_at)
                    VALUES (?, ?, '{}', CURRENT_TIMESTAMP)
                    ON CONFLICT(storage_key) DO UPDATE SET state = ?, updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, state, state),
                )

    def get_fsm_state(self, key: str) -> Optional[str]:
        """Чтение состояния FSM."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT state FROM fsm_storage WHERE storage_key = ?", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] is not None else None

    def set_fsm_data(self, key: str, data: Dict[str, Any]) -> None:
        """Сохранение полезной нагрузки FSM."""
        raw_json = json.dumps(data or {}, ensure_ascii=False)
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO fsm_storage (storage_key, state, data, updated_at)
                VALUES (?, NULL, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(storage_key) DO UPDATE SET data = ?, updated_at = CURRENT_TIMESTAMP
                """,
                (key, raw_json, raw_json),
            )

    def get_fsm_data(self, key: str) -> Dict[str, Any]:
        """Чтение полезной нагрузки FSM."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM fsm_storage WHERE storage_key = ?", (key,))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception:
                    return {}
            return {}

    def set_external_session(self, platform: str, user_id: str, data: Dict[str, Any]) -> None:
        """Сохранение сессии внешнего мессенджера (VK/MAX)."""
        raw_json = json.dumps(data or {}, ensure_ascii=False)
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO external_sessions (platform, user_id, data, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, user_id) DO UPDATE SET data = ?, updated_at = CURRENT_TIMESTAMP
                """,
                (platform, str(user_id), raw_json, raw_json),
            )

    def delete_external_session(self, platform: str, user_id: str) -> None:
        """Удаление завершённой внешней сессии."""
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM external_sessions WHERE platform = ? AND user_id = ?",
                (platform, str(user_id)),
            )

    def get_all_external_sessions(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Загрузка всех незавершённых сессий внешних мессенджеров при старте."""
        sessions: List[Tuple[str, str, Dict[str, Any]]] = []
        with self.connection() as conn:  # type: ignore
            cursor = conn.cursor()
            cursor.execute("SELECT platform, user_id, data FROM external_sessions")
            for plat, uid, raw_json in cursor.fetchall():
                try:
                    d = json.loads(raw_json) if raw_json else {}
                except Exception:
                    d = {}
                sessions.append((plat, uid, d))
        return sessions
