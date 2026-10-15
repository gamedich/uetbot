from .candidate import candidate_router
from .hr import hr_router

try:
    from .tech import tech_router
except ImportError:
    tech_router = None

__all__ = ["candidate_router", "hr_router", "tech_router"]

