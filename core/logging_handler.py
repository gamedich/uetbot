import collections
import logging
from typing import List


class MemoryLogHandler(logging.Handler):
    """Кольцевой буфер оперативных логов для удаленной диагностики через Telegram (/logs)."""

    def __init__(self, capacity: int = 120) -> None:
        super().__init__()
        self.buffer: collections.deque[str] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            pass

    def get_logs(self, n: int = 30) -> List[str]:
        """Возвращает последние n строк логов."""
        return list(self.buffer)[-n:]
