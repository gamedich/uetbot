"""
Пакет сервисного слоя МУП «Ульяновскэлектротранс».
"""

from .candidate_service import CandidateService
from .compliance_service import ComplianceService
from .doc_generator import generate_destruction_act_docx
from .interview_service import InterviewService

__all__ = [
    "CandidateService",
    "ComplianceService",
    "InterviewService",
    "generate_destruction_act_docx",
]
