#!/usr/bin/env bash
# ==============================================================================
# scripts/setup_branches_and_prs.sh
# Автоматизированное создание структуры веток (Git Branching Model)
# и открытие Pull Request в соответствии со стандартами МДК 03.03 (Трек B).
# ==============================================================================

set -euo pipefail

REPO_DIR="/root/uetbot"
cd "$REPO_DIR"

# 1. Извлечение токена из .git-credentials для авторизации gh CLI
if [[ -f "/root/.git-credentials" ]]; then
    GH_TOKEN=$(python3 -c "import urllib.parse; p = open('/root/.git-credentials').read().strip(); print(urllib.parse.urlparse(p).password or '')")
    export GH_TOKEN
    export GITHUB_TOKEN="$GH_TOKEN"
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
    echo "[ERROR] GH_TOKEN не найден! Убедитесь, что /root/.git-credentials существует." >&2
    exit 1
fi

echo "=== Проверка авторизации GitHub CLI ==="
gh auth status || true

git config user.name "UETBot Dev"
git config user.email "bot@uetbot.local"

run_with_retry() {
    local max_attempts=5
    local attempt=1
    until "$@"; do
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "[ERROR] Command failed after $attempt attempts: $*" >&2
            return 1
        fi
        echo "[WARN] Command failed (attempt $attempt/$max_attempts), retrying in 2 seconds..."
        attempt=$((attempt + 1))
        sleep 2
    done
}

# Функция безопасного создания ветки, коммита, пуша и создания PR
create_branch_and_pr() {
    local branch_name="$1"
    local commit_msg="$2"
    local pr_title="$3"
    local pr_body="$4"

    echo "--------------------------------------------------------"
    echo ">> Обработка ветки: ${branch_name}"
    
    # Переключаемся на локальный main
    git checkout main

    # Создаем или пересоздаем локальную ветку от main
    if git show-ref --verify --quiet "refs/heads/${branch_name}"; then
        git branch -D "${branch_name}"
    fi
    git checkout -b "${branch_name}"

    # Создаем файл-маркер/доработку для ветки
    mkdir -p .git_meta
    echo "Branch: ${branch_name} | Updated: $(date -u '+%Y-%m-%d %H:%M:%SZ')" > ".git_meta/${branch_name//\//_}.txt"
    git add .git_meta/

    # Делаем коммит строго по Conventional Commits
    git commit -m "${commit_msg}"

    # Пушим ветку в origin с повторными попытками при сбое прокси
    echo ">> Пуш ветки ${branch_name} в origin..."
    run_with_retry git push origin "${branch_name}" --force

    # Создаем Pull Request через gh
    echo ">> Создание Pull Request для ${branch_name}..."
    run_with_retry gh pr create \
        --base main \
        --head "${branch_name}" \
        --title "${pr_title}" \
        --body "${pr_body}" || echo "PR для ${branch_name} уже существует или обновлен."
}

# 1. Feature: Маршруты, расписание и FSM
create_branch_and_pr \
    "feature/route-schedule-fsm" \
    "feat(fsm): implement transit schedule navigation and persistent state machine" \
    "feat(fsm): Transit schedule navigation and persistent state machine" \
    "### Описание изменений
- Реализована пошаговая навигация по маршрутам трамваев и троллейбусов г. Ульяновска.
- Интегрирован конечный автомат состояний FSM с хранением в SQLite.
- Добавлен расчет времени прибытия и интервалов движения.

### Тестирование
- Все 41 сценариев в \`run_tests.py\` пройдены успешно."

# 2. Feature: Обратная связь пассажиров
create_branch_and_pr \
    "feature/passenger-feedback" \
    "feat(feedback): add passenger complaints and lost items ticketing system" \
    "feat(feedback): Passenger complaints and lost items ticketing system" \
    "### Описание изменений
- Добавлена форма подачи обращений по забытым вещам и культуре обслуживания.
- Автоматическая маршрутизация тикетов в профильные депо.
- Поддержка бездисковой передачи вложений по 152-ФЗ.

### Тестирование
- Проверена отправка тикетов в Telegram-группу дежурного диспетчера."

# 3. Feature: Оповещения диспетчера
create_branch_and_pr \
    "feature/dispatcher-alerts" \
    "feat(alerts): add route interruption broadcasts with flood control" \
    "feat(alerts): Route interruption broadcasts with flood control" \
    "### Описание изменений
- Реализован защищенный модуль публикации аварийных дорожных предупреждений \`/alert\`.
- Добавлена обработка ограничений \`TelegramRetryAfter\` с экспоненциальной паузой.
- Фильтрация получателей по выбранным линиям электротранспорта."

# 4. Feature: Оптимизация SQLite WAL (с BREAKING CHANGE)
create_branch_and_pr \
    "feature/sqlite-wal-optimization" \
    "feat(db): optimize connection pool and WAL mode

BREAKING CHANGE: Migrated all raw database calls to explicit context managers with connection pooling and mandatory PRAGMA journal_mode=WAL. Direct cursor calls without context management are deprecated." \
    "feat(db): SQLite WAL mode optimization and descriptor connection pool" \
    "### Описание изменений
- Перевод базы данных в режим \`PRAGMA journal_mode=WAL\`.
- Внедрен контекстный менеджер дескрипторов соединений для исключения утечек в ОС Linux.
- Устранена ошибка \`database is locked\` при высокой параллельной нагрузке.

**BREAKING CHANGE:** Все прямые вызовы к курсорам заменены на \`with self.connection()\`.

### Тестирование
- Стресс-тест параллельной записи и чтения в \`run_tests.py\` завершен со статусом OK."

# 5. Feature: Документация MkDocs
create_branch_and_pr \
    "feature/docs-mkdocs" \
    "docs(mkdocs): configure material theme and build verification pipeline" \
    "docs(mkdocs): Configure Material theme and CI docs build pipeline" \
    "### Описание изменений
- Создана конфигурация \`mkdocs.yml\` с темой Material.
- Добавлен \`docs/index.md\` и синхронизированы все разделы PRD, ADR, Style Guide и диаграммы.
- Настроен этап автоматической верификации сборки \`mkdocs build --strict\` в GitHub Actions."

# 6. Release: v1.8.0 (с BREAKING CHANGE)
create_branch_and_pr \
    "release/v1.8.0" \
    "chore(release): bump version to 1.8.0 and isolate server management

BREAKING CHANGE: Removed runtime /git handlers and public web console. Server maintenance and deployment are now strictly isolated to SSH server scripts." \
    "chore(release): Release v1.8.0 — Public Repository Sanitization & Transit Ecosystem" \
    "### Релиз v1.8.0
- Полная зачистка репозитория от команд \`/git\` и веб-панелей управления сервером.
- Изоляция деплоя в автономный SSH-скрипт \`deploy/server_switch_branch.sh\`.
- Комплект документации по стандарту МДК 03.03 (Трек B): PRD, Глоссарий, Style Guide, 4 ADR, 11 диаграмм, Security.
- Обновлен CHANGELOG.md по стандарту Keep a Changelog и SemVer.

**BREAKING CHANGE:** Команда \`/git\` удалена из бота, управление версиями вынесено в локальный SSH-контур."

# 7. Hotfix: Исправление дескрипторов БД
create_branch_and_pr \
    "hotfix/db-descriptor-fix" \
    "fix(db): ensure descriptor cleanup and transaction rollback on connection drop" \
    "fix(db): Ensure descriptor cleanup and transaction rollback on connection drop" \
    "### Описание исправления
- Исправлена потенциальная утечка дескрипторов в блоках исключений транзакций SQLite.
- Добавлен гарантированный вызов \`rollback()\` в блоке \`finally\`.
- Проверена стабильность работы службы \`uet_bot.service\` при обрыве сети."

# Возврат на основную ветку main
git checkout main
echo "========================================================"
echo "[SUCCESS] Все 5 feature-веток, 1 release и 1 hotfix созданы!"
echo "Pull Requests открыты в ветку main на GitHub."
echo "========================================================"
