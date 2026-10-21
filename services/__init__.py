"""
Пакет сервисного слоя МУП «Ульяновскэлектротранс».
"""

from .candidate_service import CandidateService
from .compliance_service import ComplianceService
from .doc_generator import generate_destruction_act_docx
from .domain_rules import (
    ULYANOVSK_TRAM_ROUTES,
    calculate_age_from_birth_date,
    calculate_shift_pay,
    validate_candidate_age_for_role,
    validate_phone_number,
    validate_russian_fio,
    validate_tram_route,
)
from .interview_service import InterviewService

__all__ = [
    "CandidateService",
    "ComplianceService",
    "InterviewService",
    "generate_destruction_act_docx",
    "ULYANOVSK_TRAM_ROUTES",
    "calculate_age_from_birth_date",
    "calculate_shift_pay",
    "validate_candidate_age_for_role",
    "validate_phone_number",
    "validate_russian_fio",
    "validate_tram_route",
]
