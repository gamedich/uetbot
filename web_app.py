# -*- coding: utf-8 -*-
"""
Веб-панель управления и Telegram Mini App (TMA) для МУП «Ульяновскэлектротранс».
Реализовано на асинхронном движке aiohttp.web в соответствии с требованиями
безопасности, производительности и современным адаптивным дизайном.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiohttp import web

from config import CONFIG
from database import ResumeDB

logger = logging.getLogger("UET_WEB_APP")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>МУП «Ульяновскэлектротранс» — Кадровая панель</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0d1117;
            --surface-color: #161b22;
            --surface-border: #30363d;
            --primary: #1f6feb;
            --primary-hover: #388bfd;
            --text-main: #f0f6fc;
            --text-muted: #8b949e;
            --success: #238636;
            --warning: #d29922;
            --danger: #da3633;
            --badge-blue: #0969da;
            --radius-lg: 16px;
            --radius-md: 10px;
            --font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: var(--font-family);
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .container {
            max-width: 1200px;
            width: 100%;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        /* HEADER */
        .header {
            background: linear-gradient(135deg, rgba(31, 111, 235, 0.15), rgba(22, 27, 34, 0.95));
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-lg);
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }

        .brand-title {
            font-size: 24px;
            font-weight: 800;
            display: flex;
            align-items: center;
            gap: 10px;
            letter-spacing: -0.5px;
        }

        .brand-subtitle {
            font-size: 14px;
            color: var(--text-muted);
            font-weight: 500;
        }

        .status-badges {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .badge {
            font-size: 12px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 20px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .badge-env-test {
            background: rgba(210, 153, 34, 0.2);
            color: #e3b341;
            border: 1px solid rgba(210, 153, 34, 0.4);
        }

        .badge-env-prod {
            background: rgba(35, 134, 54, 0.2);
            color: #3fb950;
            border: 1px solid rgba(35, 134, 54, 0.4);
        }

        .badge-live {
            background: rgba(31, 111, 235, 0.2);
            color: #58a6ff;
            border: 1px solid rgba(31, 111, 235, 0.4);
        }

        /* KPI STATS CARDS */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
        }

        .stat-card {
            background-color: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-md);
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }

        .stat-card:hover {
            transform: translateY(-2px);
            border-color: var(--primary);
        }

        .stat-label {
            font-size: 13px;
            color: var(--text-muted);
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .stat-value {
            font-size: 28px;
            font-weight: 700;
            color: var(--text-main);
        }

        /* TABLE SECTION */
        .section-card {
            background-color: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-lg);
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
        }

        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }

        .section-title {
            font-size: 18px;
            font-weight: 700;
        }

        .search-box {
            padding: 8px 14px;
            background-color: var(--bg-color);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-md);
            color: var(--text-main);
            font-size: 14px;
            outline: none;
            width: 250px;
            transition: border-color 0.2s ease;
        }

        .search-box:focus {
            border-color: var(--primary);
        }

        .table-responsive {
            overflow-x: auto;
            border-radius: var(--radius-md);
            border: 1px solid var(--surface-border);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 14px;
        }

        th {
            background-color: #1c2128;
            color: var(--text-muted);
            font-weight: 600;
            padding: 12px 16px;
            border-bottom: 1px solid var(--surface-border);
        }

        td {
            padding: 14px 16px;
            border-bottom: 1px solid var(--surface-border);
            vertical-align: middle;
        }

        tr:last-child td {
            border-bottom: none;
        }

        tr:hover td {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .cand-status {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }

        .status-new {
            background: rgba(31, 111, 235, 0.2);
            color: #58a6ff;
        }

        .status-hired {
            background: rgba(35, 134, 54, 0.2);
            color: #3fb950;
        }

        .status-rejected {
            background: rgba(218, 54, 51, 0.2);
            color: #f85149;
        }

        .status-work {
            background: rgba(210, 153, 34, 0.2);
            color: #e3b341;
        }

        .btn-refresh {
            background-color: var(--primary);
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: var(--radius-md);
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: background-color 0.2s ease;
        }

        .btn-refresh:hover {
            background-color: var(--primary-hover);
        }

        .footer {
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            padding: 10px 0;
        }

        @media (max-width: 768px) {
            body {
                padding: 12px;
            }
            .brand-title {
                font-size: 20px;
            }
            .search-box {
                width: 100%;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- HEADER -->
        <header class="header">
            <div class="header-top">
                <div class="brand-title">
                    🚋 МУП «Ульяновскэлектротранс»
                </div>
                <div class="status-badges">
                    <span id="badgeEnv" class="badge badge-env-test">Среда: TEST</span>
                    <span id="badgeDb" class="badge badge-live">БД: resumes.db</span>
                    <span class="badge badge-live">152-ФЗ Активен</span>
                </div>
            </div>
            <div class="brand-subtitle">
                Кадровый мониторинг соискателей, водительского состава и персонала депо
            </div>
        </header>

        <!-- KPI CARDS -->
        <section class="stats-grid">
            <div class="stat-card">
                <span class="stat-label">Всего соискателей</span>
                <span id="statTotal" class="stat-value">...</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Новые анкеты</span>
                <span id="statNew" class="stat-value" style="color: #58a6ff;">...</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">В обработке</span>
                <span id="statInProgress" class="stat-value" style="color: #e3b341;">...</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Приняты в депо</span>
                <span id="statHired" class="stat-value" style="color: #3fb950;">...</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Отказ / ЧС</span>
                <span id="statRejected" class="stat-value" style="color: #f85149;">...</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Обращений (/ask)</span>
                <span id="statInquiries" class="stat-value">...</span>
            </div>
        </section>

        <!-- CANDIDATES TABLE -->
        <main class="section-card">
            <div class="section-header">
                <h2 class="section-title">📋 Реестр соискателей (последние заявки)</h2>
                <div style="display: flex; gap: 10px; align-items: center; width: 100%; max-width: 400px; justify-content: flex-end;">
                    <input type="text" id="searchInput" class="search-box" placeholder="Поиск по ФИО или вакансии...">
                    <button class="btn-refresh" onclick="loadData()">🔄 Обновить</button>
                </div>
            </div>

            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>№</th>
                            <th>Дата</th>
                            <th>ФИО соискателя</th>
                            <th>Вакансия</th>
                            <th>Телефон</th>
                            <th>Статус</th>
                            <th>Депо / Время</th>
                        </tr>
                    </thead>
                    <tbody id="candidatesTbody">
                        <tr>
                            <td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">Загрузка данных...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </main>

        <footer class="footer">
            МУП «Ульяновскэлектротранс» • Сервер кадровой автоматизации • Порт 8080
        </footer>
    </div>

    <script>
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
        }

        let allCandidates = [];

        async function loadData() {
            try {
                const [statsRes, candRes] = await Promise.all([
                    fetch('/api/stats'),
                    fetch('/api/candidates')
                ]);

                if (statsRes.ok) {
                    const stats = await statsRes.json();
                    document.getElementById('statTotal').innerText = stats.total || 0;
                    document.getElementById('statNew').innerText = stats.new || 0;
                    document.getElementById('statInProgress').innerText = stats.in_progress || 0;
                    document.getElementById('statHired').innerText = stats.hired || 0;
                    document.getElementById('statRejected').innerText = stats.rejected || 0;
                    document.getElementById('statInquiries').innerText = stats.inquiries || 0;

                    const envEl = document.getElementById('badgeEnv');
                    envEl.innerText = 'Среда: ' + (stats.env || 'TEST');
                    if (stats.env === 'PROD') {
                        envEl.className = 'badge badge-env-prod';
                    } else {
                        envEl.className = 'badge badge-env-test';
                    }

                    document.getElementById('badgeDb').innerText = 'БД: ' + (stats.active_db || 'resumes.db');
                }

                if (candRes.ok) {
                    allCandidates = await candRes.json();
                    renderCandidates(allCandidates);
                }
            } catch (err) {
                console.error("Failed to load data:", err);
            }
        }

        function renderCandidates(list) {
            const tbody = document.getElementById('candidatesTbody');
            if (!list || list.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет зарегистрированных анкет</td></tr>';
                return;
            }

            let html = '';
            list.forEach(c => {
                let statusClass = 'status-work';
                const st = (c.status || '').toLowerCase();
                if (st.includes('нов')) statusClass = 'status-new';
                else if (st.includes('принят')) statusClass = 'status-hired';
                else if (st.includes('отказ')) statusClass = 'status-rejected';

                const depotInfo = (c.interview_depot || '') + (c.interview_time ? ' (' + c.interview_time + ')' : '');

                html += `<tr>
                    <td><b>#${c.ticket_id}</b></td>
                    <td style="color: var(--text-muted);">${c.created_at || '—'}</td>
                    <td><b>${escapeHtml(c.full_name || 'Не указано')}</b></td>
                    <td>${escapeHtml(c.vacancy || '—')}</td>
                    <td><code>${escapeHtml(c.phone || '—')}</code></td>
                    <td><span class="cand-status ${statusClass}">${escapeHtml(c.status || 'Новая')}</span></td>
                    <td style="color: var(--text-muted);">${escapeHtml(depotInfo || '—')}</td>
                </tr>`;
            });
            tbody.innerHTML = html;
        }

        function escapeHtml(text) {
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        document.getElementById('searchInput').addEventListener('input', function(e) {
            const query = e.target.value.toLowerCase().trim();
            if (!query) {
                renderCandidates(allCandidates);
                return;
            }
            const filtered = allCandidates.filter(c => 
                (c.full_name && c.full_name.toLowerCase().includes(query)) ||
                (c.vacancy && c.vacancy.toLowerCase().includes(query)) ||
                (c.phone && c.phone.includes(query)) ||
                (String(c.ticket_id).includes(query))
            );
            renderCandidates(filtered);
        });

        loadData();
        setInterval(loadData, 30000);
    </script>
</body>
</html>
"""


class WebPanel:
    def __init__(self, db: ResumeDB):
        self.db = db
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/app", self.handle_index)
        self.app.router.add_get("/api/stats", self.handle_api_stats)
        self.app.router.add_get("/api/candidates", self.handle_api_candidates)
        self.app.router.add_get("/api/health", self.handle_api_health)

    async def handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=HTML_TEMPLATE, content_type="text/html", charset="utf-8")

    async def handle_api_stats(self, request: web.Request) -> web.Response:
        env = CONFIG.get("ENVIRONMENT", "TEST").upper()
        active_db = self.db.get_setting("active_db_target", "resumes.db")
        is_test = (active_db == "resumes_test.db")

        try:
            with self.db.connection(is_test=is_test) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM candidates")
                total = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Новая'")
                new_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM candidates WHERE status IN ('В обработке', 'Приглашен')")
                in_prog = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Принят'")
                hired = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM candidates WHERE status = 'Отказ'")
                rejected = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM inquiries")
                inquiries = cursor.fetchone()[0]

            return web.json_response({
                "env": env,
                "active_db": active_db,
                "total": total,
                "new": new_count,
                "in_progress": in_prog,
                "hired": hired,
                "rejected": rejected,
                "inquiries": inquiries,
            })
        except Exception as e:
            logger.error("Ошибка API stats: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_candidates(self, request: web.Request) -> web.Response:
        active_db = self.db.get_setting("active_db_target", "resumes.db")
        is_test = (active_db == "resumes_test.db")

        try:
            with self.db.connection(is_test=is_test) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ticket_id, created_at, full_name, vacancy, phone, status,
                           interview_depot, interview_time
                    FROM candidates
                    ORDER BY ticket_id DESC
                    LIMIT 100
                    """
                )
                rows = cursor.fetchall()

            result = []
            for r in rows:
                result.append({
                    "ticket_id": r[0],
                    "created_at": r[1] or "",
                    "full_name": r[2] or "",
                    "vacancy": r[3] or "",
                    "phone": r[4] or "",
                    "status": r[5] or "Новая",
                    "interview_depot": r[6] or "",
                    "interview_time": r[7] or "",
                })
            return web.json_response(result)
        except Exception as e:
            logger.error("Ошибка API candidates: %s", e)
            return web.json_response([], status=500)

    async def handle_api_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "time": datetime.now().isoformat(),
            "version": CONFIG.get("BOT_VERSION", "1.6.0"),
        })


async def start_web_panel_task(db: ResumeDB, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Запуск фонового веб-сервера кадровой панели."""
    panel = WebPanel(db)
    runner = web.AppRunner(panel.app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🌐 Веб-панель МУП «Ульяновскэлектротранс» запущена на http://%s:%s", host, port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
