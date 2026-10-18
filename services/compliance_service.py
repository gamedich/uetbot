"""
Сервисный слой комплаенс-контроля и скоринга соискателей
МУП «Ульяновскэлектротранс».

Соответствие требованиям:
- Федеральный закон от 11.06.2022 № 155-ФЗ (ст. 328.1 ТК РФ) — ограничение допуска
  к управлению городским пассажирским транспортом (трамваи, троллейбусы);
- Статья 86 УК РФ (погашение и снятие судимости);
- Регламент предприятия по проверке дубликатов, ЧС и 90-дневного кулдауна;
- Анализ водительских категорий и триггеры бесплатного обучения со стипендией.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Union

from common import db


class ComplianceService:
    """Сервис проверки благонадежности, правовых ограничений и квалификации кандидатов."""

    DRIVER_VACANCIES = ("водитель трамвая", "водитель троллейбуса", "водитель")

    @classmethod
    def is_driver_position(cls, vacancy: str) -> bool:
        """Определяет, относится ли должность к управлению пассажирским транспортом."""
        v = (vacancy or "").lower().strip()
        return any(d in v for d in cls.DRIVER_VACANCIES)

    @classmethod
    def check_criminal_record_risk(cls, vacancy: str, criminal_record: str) -> Tuple[bool, str]:
        """
        Проверка ограничений по ст. 328.1 ТК РФ и ст. 86 УК РФ.
        Возвращает: (has_critical_risk, description)
        """
        crim_clean = (criminal_record or "").strip().lower()
        is_driver = cls.is_driver_position(vacancy)

        # Если судимости нет
        if not crim_clean or crim_clean in ("нет", "нет (ст. 86 ук рф)", "отсутствует", "не судим", "чисто"):
            return False, "Судимость отсутствует (ст. 86 УК РФ соблюдена)."

        # Если должность водительская — действует прямой запрет ст. 328.1 ТК РФ
        if is_driver:
            return True, (
                "⛔ <b>БЛОКИРУЮЩИЙ РИСК (ст. 328.1 ТК РФ):</b>\n"
                "Лица с неснятой/непогашенной судимостью за преступления против общественной безопасности, "
                "здоровья населения или тяжкие преступления <b>НЕ ДОПУСКАЮТСЯ</b> к управлению городским электротранспортом."
            )

        return False, (
            "⚠️ <b>ВНИМАНИЕ:</b> Указана судимость. Для неводительских должностей (слесарь, рабочий) "
            "требуется индивидуальное согласование со службой безопасности предприятия."
        )

    @classmethod
    def analyze_driver_licenses(cls, vacancy: str, license_str: str) -> Dict[str, Any]:
        """
        Анализ категорий водительских прав под выбранную должность:
        - Tm: трамвай
        - Tb: троллейбус
        - B, C: возможность переподготовки
        - Отсутствие: программа обучения со стипендией
        """
        lic = (license_str or "").strip().upper()
        v = (vacancy or "").lower()

        is_tram = "трамва" in v
        is_trolley = "троллейбус" in v

        # Проверка отсутствия прав
        is_none = any(neg in lic for neg in ["НЕТ", "ОТСУТ", "БЕЗ ПРАВ", "НЕ ИМЕЮ"])

        if is_none:
            has_tm = False
            has_tb = False
            has_b = False
            has_c = False
            has_d = False
        else:
            has_tm = bool(re.search(r"(?<![А-ЯA-Z])(TM|ТМ)(?![А-ЯA-Z])", lic))
            has_tb = bool(re.search(r"(?<![А-ЯA-Z])(TB|ТБ)(?![А-ЯA-Z])", lic))
            has_b = bool(re.search(r"(?<![А-ЯA-Z])(B|В)(?![А-ЯA-Z])", lic))
            has_c = bool(re.search(r"(?<![А-ЯA-Z])(C|С)(?![А-ЯA-Z])", lic))
            has_d = bool(re.search(r"(?<![А-ЯA-Z])(D|Д)(?![А-ЯA-Z])", lic))

        result = {
            "is_qualified": False,
            "offer_training": False,
            "training_type": None,
            "note": "",
        }

        if is_tram:
            if has_tm:
                result["is_qualified"] = True
                result["note"] = "✅ Категория «Tm» открыта. Готов к стажировке на линии без обучения."
            elif has_b or has_c or has_d:
                result["offer_training"] = True
                result["training_type"] = "retraining"
                result["note"] = (
                    "🎓 Есть категория B/C/D. Рекомендуется ускоренная переподготовка на водителя трамвая («Tm»)."
                )
            else:
                result["offer_training"] = True
                result["training_type"] = "full_school"
                result["note"] = (
                    "🎓 Нет прав. Направляется в Учебный центр предприятия (бесплатное обучение + стипендия 25 000 ₽)."
                )

        elif is_trolley:
            if has_tb:
                result["is_qualified"] = True
                result["note"] = "✅ Категория «Tb» открыта. Готов к стажировке в депо №3."
            elif has_b or has_c or has_d:
                result["offer_training"] = True
                result["training_type"] = "retraining"
                result["note"] = "🎓 Есть категория B/C/D. Льготная переподготовка на категорию «Tb»."
            else:
                result["offer_training"] = True
                result["training_type"] = "full_school"
                result["note"] = "🎓 Нет прав. Обучение на водителя троллейбуса за счет МУП «УЭТ» со стипендией."

        else:
            result["is_qualified"] = True
            result["note"] = "ℹ️ Для данной специальности наличие прав Tm/Tb не является обязательным."

        return result

    @classmethod
    def score_candidate(cls, cand_data: Union[Tuple[Any, ...], Dict[str, Any]]) -> Dict[str, Any]:
        """
        Комплексный скоринг рисков соискателя:
        1. Проверка ЧС предприятия.
        2. Проверка 90-дневного кулдауна.
        3. Комплаенс ст. 328.1 ТК РФ.
        4. Анализ квалификации и обучения.
        """
        if isinstance(cand_data, dict):
            user_id = str(cand_data.get("user_id", ""))
            platform = str(cand_data.get("platform", "tg"))
            vacancy = str(cand_data.get("vacancy", ""))
            criminal = str(cand_data.get("criminal_record", "Нет"))
            licenses = str(cand_data.get("driver_license", "Нет"))
        else:
            user_id = str(cand_data[2]) if len(cand_data) > 2 else ""
            platform = str(cand_data[1]) if len(cand_data) > 1 else "tg"
            vacancy = str(cand_data[5]) if len(cand_data) > 5 else ""
            licenses = str(cand_data[12]) if len(cand_data) > 12 else "Нет"
            criminal = str(cand_data[18]) if len(cand_data) > 18 else "Нет"

        flags: List[str] = []
        recommendations: List[str] = []
        risk_level = "LOW"

        # 1. Черный список
        if db.is_blocked(user_id):
            risk_level = "CRITICAL"
            flags.append("⛔ Пользователь находится в локальном Черном списке (антиспам/нарушения).")

        # 2. Кулдаун отказов
        can_apply, reason, info = db.check_candidate_can_apply(user_id, platform=platform)
        if not can_apply and reason == "rejected_cooldown":
            if risk_level != "CRITICAL":
                risk_level = "HIGH"
            days = info.get("days_left", 90) if info else 90
            flags.append(f"⏳ Действует кулдаун после предыдущего отказа (осталось {days} дн.).")

        # 3. Судимость (ст. 328.1 ТК РФ)
        has_tk_block, tk_text = cls.check_criminal_record_risk(vacancy, criminal)
        if has_tk_block:
            risk_level = "CRITICAL"
            flags.append(tk_text)
        elif "ВНИМАНИЕ" in tk_text:
            if risk_level == "LOW":
                risk_level = "MEDIUM"
            flags.append(tk_text)

        # 4. Анализ прав и обучения
        lic_analysis = cls.analyze_driver_licenses(vacancy, licenses)
        if lic_analysis.get("note"):
            recommendations.append(lic_analysis["note"])

        # Формирование HTML-блока
        badge = {
            "LOW": "🟢 <b>НИЗКИЙ РИСК (Рекомендован)</b>",
            "MEDIUM": "🟡 <b>СРЕДНИЙ РИСК (Требует внимания)</b>",
            "HIGH": "🟠 <b>ВЫСОКИЙ РИСК (Кулдаун/Повтор)</b>",
            "CRITICAL": "🔴 <b>КРИТИЧЕСКИЙ РИСК (Отказ по регламенту)</b>",
        }.get(risk_level, "⚪ <b>НЕ ОПРЕДЕЛЕН</b>")

        html_lines = [
            "🛡️ <b>СКОРИНГ БЕЗОПАСНОСТИ СОИСКАТЕЛЯ:</b>",
            f"Уровень риска: {badge}",
        ]

        if flags:
            html_lines.append("<b>Факторы риска:</b>")
            for f in flags:
                html_lines.append(f"• {f}")

        if recommendations:
            html_lines.append("<b>Рекомендации отдела кадров:</b>")
            for r in recommendations:
                html_lines.append(f"• {r}")

        summary_html = "\n".join(html_lines)

        return {
            "risk_level": risk_level,
            "flags": flags,
            "recommendations": recommendations,
            "badge": badge,
            "badge_html": summary_html,
            "summary_html": summary_html,
        }
