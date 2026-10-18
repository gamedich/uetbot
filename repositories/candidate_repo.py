"""
Слой репозиториев для взаимодействия с БД SQLite / DAL.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("UET_REPOSITORY")


class BaseRepository:
    """Базовый класс репозитория, инкапсулирующий экземпляр ResumeDB."""

    def __init__(self, db: Any):
        self.db = db


class CandidateRepository(BaseRepository):
    """Репозиторий кандидатов и анкет."""

    def get_by_id(self, ticket_id: int) -> Optional[Tuple[Any, ...]]:
        return self.db.get_candidate(ticket_id)

    def get_all(self, limit: int = 50, offset: int = 0, status_filter: Optional[str] = None) -> List[Tuple[Any, ...]]:
        return self.db.get_candidates(limit=limit, offset=offset, status_filter=status_filter)

    def add(
        self,
        platform: str,
        user_id: str,
        full_name: str,
        phone: str,
        vacancy: str,
        experience: str,
        citizenship: str = "РФ",
        status: str = "Новая",
        age: Optional[int] = None,
        notes: str = "",
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> int:
        return self.db.add_candidate(
            platform=platform,
            user_id=user_id,
            full_name=full_name,
            phone=phone,
            vacancy=vacancy,
            experience=experience,
            citizenship=citizenship,
            status=status,
            age=age,
            notes=notes,
            extra_data=extra_data,
        )

    def update_status(self, ticket_id: int, status: str, admin_name: str = "") -> bool:
        return self.db.update_candidate_status(ticket_id, status, admin_name=admin_name)

    def update_notes(self, ticket_id: int, notes: str) -> bool:
        return self.db.update_candidate_notes(ticket_id, notes)

    def delete(self, ticket_id: int, reason: str = "Удаление оператором") -> bool:
        return self.db.delete_candidate_fully(ticket_id, reason=reason)

    def check_can_apply(self, user_id: str, platform: str = "tg") -> Tuple[bool, Optional[str], Dict[str, Any]]:
        return self.db.check_candidate_can_apply(user_id, platform=platform)


class AuditRepository(BaseRepository):
    """Репозиторий аудита действий операторов и логов уничтожения ПДн (152-ФЗ)."""

    def log_action(self, admin_id: int, action: str, details: str = "", ip_address: str = "") -> None:
        self.db.log_audit_action(admin_id, action, details, ip_address)

    def log_destruction(self, ticket_id: int, user_id: str, platform: str, reason: str) -> None:
        self.db.log_pdn_destruction(ticket_id, user_id, platform, reason)

    def get_destruction_logs(self, limit: int = 100) -> List[Tuple[Any, ...]]:
        return self.db.get_destruction_logs(limit=limit)
