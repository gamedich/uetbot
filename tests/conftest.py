"""
Глобальные фикстуры pytest для тестирования Telegram-бота МУП «УЭТ».
Обеспечивают 100% изоляцию тестов от боевой и локальной баз данных (12-Factor App).
"""

import os
from pathlib import Path
import pytest

from database import ResumeDB


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch, tmp_path: Path):
    """
    Автоматическая изоляция окружения для каждого теста:
    1. Перенаправляет DB_PATH на изолированный временный файл в tmp_path.
    2. Устанавливает тестовый ENVIRONMENT=TEST.
    3. Задает безопасные тестовые токены и идентификаторы.
    """
    test_db_file = tmp_path / "isolated_test.db"
    monkeypatch.setenv("ENVIRONMENT", "TEST")
    monkeypatch.setenv("DB_PATH", str(test_db_file))
    monkeypatch.setenv("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789")
    monkeypatch.setenv("TG_BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789")
    monkeypatch.setenv("SUPER_ADMIN_ID", "123456789")
    monkeypatch.setenv("HR_GROUP_ID", "-1001234567890")
    yield


@pytest.fixture
def tmp_db(tmp_path: Path) -> ResumeDB:
    """Фикстура изолированной SQLite базы данных в директории tmp_path."""
    db_file = str(tmp_path / "pytest_fixture.db")
    test_file = str(tmp_path / "pytest_fixture_test.db")
    return ResumeDB(db_path=db_file, test_db_path=test_file)


@pytest.fixture
def memory_db() -> ResumeDB:
    """Фикстура чистой in-memory базы данных (:memory:)."""
    return ResumeDB(db_path=":memory:", test_db_path=":memory:")
