"""
Тесты безопасности и криптографического хеширования паролей (152-ФЗ РФ).
Проверяют стойкость scrypt, уникальность соли и обратную совместимость.
"""

from database import ResumeDB


class TestPasswordSecurity:
    """Тестирование хеширования и сверки паролей сотрудников и соискателей."""

    def test_scrypt_hash_generation(self):
        pwd = "SecretPassword123!"
        h1 = ResumeDB.hash_password(pwd)
        h2 = ResumeDB.hash_password(pwd)

        assert "$" in h1
        assert "$" in h2
        # Уникальность соли: два хеша от одного пароля должны быть разными
        assert h1 != h2

    def test_verify_password_correct(self):
        pwd = "MySuperSecurePassword"
        h = ResumeDB.hash_password(pwd)

        assert ResumeDB.verify_password(pwd, h) is True

    def test_verify_password_wrong(self):
        pwd = "CorrectPassword"
        h = ResumeDB.hash_password(pwd)

        assert ResumeDB.verify_password("WrongPassword", h) is False
        assert ResumeDB.verify_password("", h) is False

    def test_legacy_plaintext_migration_compatibility(self):
        # Обратная совместимость с нехешированными паролями до миграции
        legacy_plaintext = "uet2026super"
        assert ResumeDB.verify_password(legacy_plaintext, legacy_plaintext) is True
        assert ResumeDB.verify_password("wrong_pwd", legacy_plaintext) is False
