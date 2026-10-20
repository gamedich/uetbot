from datetime import datetime
import logging
import os
import sqlite3
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("UET_HR_BOT")


class BackupMixin:
    """Миксин резервного копирования и восстановления базы данных (SQLite Online Backup API)."""

    def backup_database(self, backup_dir: str = "backups") -> str:
        """Создание горячей резервной копии через SQLite Online Backup API."""
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"resumes_backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_filename)

        counter = 1
        while os.path.exists(backup_path):
            backup_filename = f"resumes_backup_{timestamp}_{counter}.db"
            backup_path = os.path.join(backup_dir, backup_filename)
            counter += 1

        with self.connection() as src_conn:  # type: ignore
            with sqlite3.connect(backup_path) as dst_conn:
                src_conn.backup(dst_conn)

        return backup_path

    def list_backups(self, backup_dir: str = "backups") -> List[Dict[str, Any]]:
        """Список всех резервных копий с метаданными размера и даты создания."""
        if not os.path.exists(backup_dir):
            return []
        files = []
        for fn in os.listdir(backup_dir):
            if fn.endswith(".db"):
                fp = os.path.join(backup_dir, fn)
                st = os.stat(fp)
                files.append(
                    {
                        "filename": fn,
                        "path": fp,
                        "size_bytes": st.st_size,
                        "size_kb": round(st.st_size / 1024, 1),
                        "created_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
        files.sort(key=lambda x: (x["created_at"], x["filename"]), reverse=True)
        return files

    def restore_database(self, backup_filename: str, backup_dir: str = "backups") -> Tuple[bool, str]:
        """Горячее восстановление базы данных из бэкапа со страховочной копией."""
        clean_name = os.path.basename(backup_filename)
        backup_path = os.path.join(backup_dir, clean_name)
        if not os.path.exists(backup_path):
            return False, f"Файл бэкапа {clean_name} не найден в {backup_dir}."

        pre_restore_path = self.backup_database(backup_dir)
        pre_restore_name = os.path.basename(pre_restore_path)

        try:
            with sqlite3.connect(backup_path) as src_conn:
                with self.connection() as dst_conn:  # type: ignore
                    src_conn.backup(dst_conn)
            return True, (
                f"База успешно восстановлена из <code>{clean_name}</code>.\n"
                f"Страховочная копия создана: <code>{pre_restore_name}</code>"
            )
        except Exception as e:
            logger.error(f"Сбой восстановления БД: {e}")
            return False, f"Ошибка восстановления базы: {e}"

    def delete_backup(self, backup_filename: str, backup_dir: str = "backups") -> Tuple[bool, str]:
        """Удаление файла бэкапа."""
        clean_name = os.path.basename(backup_filename)
        backup_path = os.path.join(backup_dir, clean_name)
        if not os.path.exists(backup_path):
            return False, "Файл не найден."
        try:
            os.remove(backup_path)
            return True, f"Файл <code>{clean_name}</code> успешно удален."
        except Exception as e:
            return False, f"Ошибка при удалении: {e}"

    def cleanup_old_backups(self, keep_count: int = 5, backup_dir: str = "backups") -> Tuple[int, List[str]]:
        """Ротация архивов: сохраняет последние `keep_count` копий, старые удаляет."""
        backups = self.list_backups(backup_dir)
        if len(backups) <= keep_count:
            return 0, []
        to_delete = backups[keep_count:]
        deleted_names = []
        for b in to_delete:
            try:
                os.remove(b["path"])
                deleted_names.append(b["filename"])
            except Exception:
                pass
        return len(deleted_names), deleted_names
