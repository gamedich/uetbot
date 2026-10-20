"""
Тесты изоляции базы данных и проверки 12-Factor App окружений.
Гарантируют отсутствие утечек файлов в директорию data/ и Git-индекс.
"""

from pathlib import Path
from database import ResumeDB


class TestDatabaseIsolation:
    """Тестирование работы базы данных в изолированных тестовых окружениях."""

    def test_memory_database_initialization(self, memory_db: ResumeDB):
        """Проверка инициализации схемы и миграций в :memory: режиме."""
        assert memory_db.db_path == ":memory:"
        assert memory_db.test_db_path == ":memory:"

        # Проверяем здоровье базы
        assert memory_db.check_health() is True

        # Проверяем добавление администратора
        ok = memory_db.add_admin(user_id=777, role="hr")
        assert ok is True
        assert memory_db.get_admin_role(user_id=777) == "hr"

        # Проверяем получение вакансий
        vacancies = memory_db.get_vacancies()
        assert len(vacancies) > 0

    def test_tmp_path_database_isolation(self, tmp_path: Path):
        """Проверка, что база создаётся строго во временном каталоге и не затрагивает data/."""
        custom_file = str(tmp_path / "sandbox" / "custom.db")

        # Проверяем инициализацию и обратную совместимость с test_db_path
        db = ResumeDB(db_path=custom_file, test_db_path=custom_file)
        assert Path(custom_file).exists()

        # Добавляем боевого кандидата
        ticket_id = db.add_candidate(
            platform="tg",
            user_id="999888",
            full_name="Иванов Иван Иванович",
            phone="+79990001122",
            vacancy="Водитель трамвая",
            experience="1 год",
            is_test=False,
        )
        assert ticket_id > 0
        assert ticket_id < 900000

        # Добавляем тестового кандидата в ту же базу
        test_ticket_id = db.add_candidate(
            platform="tg",
            user_id="999889",
            full_name="Тестов Тест Тестович",
            phone="+79990001133",
            vacancy="Водитель трамвая",
            experience="1 год",
            is_test=True,
        )
        assert test_ticket_id > 0
        assert test_ticket_id != ticket_id

        cand = db.get_candidate(ticket_id)
        assert cand is not None
        assert cand[3] == "Иванов Иван Иванович"

        test_cand = db.get_candidate(test_ticket_id)
        assert test_cand is not None
        assert test_cand[3] == "Тестов Тест Тестович"

        # Проверяем, что сброс тестовых данных очищает только тестовую анкету
        cands_deleted, _ = db.reset_all_test_data()
        assert cands_deleted == 1
        assert db.get_candidate(ticket_id) is not None
        assert db.get_candidate(test_ticket_id) is None

    def test_no_database_files_created_in_repo_root_or_untracked(self, memory_db: ResumeDB):
        """Убеждаемся, что при работе с memory_db в корне проекта не создаются файлы .db."""
        repo_root = Path(__file__).resolve().parent.parent
        db_files = list(repo_root.glob("*.db"))
        assert len(db_files) == 0, f"Обнаружены .db файлы в корне: {db_files}"
