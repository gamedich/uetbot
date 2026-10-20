import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("UET_HR_BOT")


class VacancyMixin:
    """Миксин управления витриной вакансий предприятия (синхронизация сайт <-> бот)."""

    def get_vacancies(self, only_active: bool = False, is_test: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Возвращает список всех вакансий из vacancy_settings, отсортированных по sort_order ASC"""
        results = []
        with self.connection() as conn:  # type: ignore
            cur = conn.cursor()
            sql = "SELECT slug, title, category, is_active, sort_order, salary_text, description, meta_json FROM vacancy_settings"
            if only_active:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY sort_order ASC, slug ASC"
            cur.execute(sql)
            rows = cur.fetchall()
            for r in rows:
                meta = {}
                try:
                    meta = json.loads(r[7] or "{}") if r[7] else {}
                except Exception:
                    meta = {}
                results.append({
                    "slug": r[0],
                    "title": r[1],
                    "category": r[2] or "general",
                    "is_active": int(r[3]),
                    "sort_order": int(r[4]),
                    "salary_text": r[5] or "",
                    "description": r[6] or "",
                    "meta_json": r[7] or "{}",
                    "meta": meta,
                })
        return results

    def get_vacancy_by_slug(self, slug: str, is_test: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """Получает карточку вакансии по её slug"""
        with self.connection() as conn:  # type: ignore
            cur = conn.cursor()
            cur.execute(
                "SELECT slug, title, category, is_active, sort_order, salary_text, description, meta_json FROM vacancy_settings WHERE slug = ? OR title = ?",
                (slug, slug),
            )
            r = cur.fetchone()
            if r:
                meta = {}
                try:
                    meta = json.loads(r[7] or "{}") if r[7] else {}
                except Exception:
                    meta = {}
                return {
                    "slug": r[0],
                    "title": r[1],
                    "category": r[2] or "general",
                    "is_active": int(r[3]),
                    "sort_order": int(r[4]),
                    "salary_text": r[5] or "",
                    "description": r[6] or "",
                    "meta_json": r[7] or "{}",
                    "meta": meta,
                }
        return None

    def save_vacancies_batch(self, vacancies: List[Dict[str, Any]]) -> bool:
        """Атомарный upsert списка вакансий в активную базу данных"""
        with self.connection() as conn:  # type: ignore
            cur = conn.cursor()
            for v in vacancies:
                slug = str(v.get("slug") or "").strip()
                if not slug:
                    continue
                title = str(v.get("title") or "").strip()
                category = str(v.get("category") or "general").strip()
                is_active = 1 if v.get("is_active") in (1, "1", True, "true") else 0
                sort_order = int(v.get("sort_order") or 0)
                salary_text = str(v.get("salary_text") or v.get("salary") or "").strip()
                description = str(v.get("description") or v.get("desc") or "").strip()
                raw_meta = v.get("meta") or v.get("meta_json")
                if isinstance(raw_meta, dict):
                    meta_str = json.dumps(raw_meta, ensure_ascii=False)
                elif isinstance(raw_meta, str) and raw_meta.strip():
                    meta_str = raw_meta.strip()
                else:
                    meta_str = "{}"

                cur.execute(
                    """
                    INSERT INTO vacancy_settings (slug, title, category, is_active, sort_order, salary_text, description, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        title=excluded.title,
                        category=excluded.category,
                        is_active=excluded.is_active,
                        sort_order=excluded.sort_order,
                        salary_text=excluded.salary_text,
                        description=excluded.description,
                        meta_json=excluded.meta_json
                    """,
                    (slug, title, category, is_active, sort_order, salary_text, description, meta_str),
                )
            conn.commit()
        return True

    def toggle_vacancy_active(self, slug: str, is_active: Optional[int] = None) -> bool:
        """Переключает видимость вакансии"""
        with self.connection() as conn:  # type: ignore
            cur = conn.cursor()
            if is_active is None:
                cur.execute(
                    "UPDATE vacancy_settings SET is_active = (1 - is_active) WHERE slug = ? OR title = ?",
                    (slug, slug),
                )
            else:
                cur.execute(
                    "UPDATE vacancy_settings SET is_active = ? WHERE slug = ? OR title = ?",
                    (is_active, slug, slug),
                )
            conn.commit()
        return True

    async def async_get_vacancies(self, only_active: bool = False) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_vacancies, only_active)

    async def async_get_vacancy_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_vacancy_by_slug, slug)

    async def async_save_vacancies_batch(self, vacancies: List[Dict[str, Any]]) -> bool:
        return await asyncio.to_thread(self.save_vacancies_batch, vacancies)
