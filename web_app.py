# -*- coding: utf-8 -*-
"""
Веб-панель управления и личный кабинет для МУП «Ульяновскэлектротранс».
Поддерживает роли: SuperAdmin, HR-специалисты, Технические администраторы.
Обеспечивает управление воронкой кандидатов, обращениями, системными метриками,
горячим бэкапом, переключением баз (resumes.db / resumes_test.db) и экспортом в Excel/CSV.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web
from aiogram import Bot

from config import CONFIG, update_env_variable
from database import ResumeDB
from services.compliance_service import ComplianceService

logger = logging.getLogger("UET_WEB_APP")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>МУП «Ульяновскэлектротранс» — Кабинет управления</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --surface-color: #131b2e;
            --surface-card: #18223a;
            --surface-hover: #1f2c4a;
            --surface-border: #243252;
            --primary: #2563eb;
            --primary-hover: #3b82f6;
            --primary-glow: rgba(37, 99, 235, 0.25);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --success: #10b981;
            --success-bg: rgba(16, 185, 129, 0.15);
            --warning: #f59e0b;
            --warning-bg: rgba(245, 158, 11, 0.15);
            --danger: #ef4444;
            --danger-bg: rgba(239, 68, 68, 0.15);
            --purple: #8b5cf6;
            --purple-bg: rgba(139, 92, 246, 0.15);
            --radius-xl: 18px;
            --radius-lg: 12px;
            --radius-md: 8px;
            --font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: var(--font-family);
            -webkit-tap-highlight-color: transparent;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .container {
            max-width: 1400px;
            width: 100%;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        /* HEADER */
        .header {
            background: linear-gradient(135deg, rgba(37, 99, 235, 0.18), rgba(19, 27, 46, 0.95));
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-xl);
            padding: 20px 24px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-logo {
            font-size: 30px;
            line-height: 1;
        }

        .brand-title {
            font-size: 20px;
            font-weight: 800;
            letter-spacing: -0.5px;
            color: var(--text-main);
        }

        .brand-subtitle {
            font-size: 13px;
            color: var(--text-muted);
            font-weight: 500;
        }

        .header-controls {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        .badge-btn {
            font-size: 12px;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 20px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            transition: var(--transition);
            border: 1px solid transparent;
            user-select: none;
        }

        .badge-btn:hover {
            transform: scale(1.03);
        }

        .badge-db-prod {
            background: var(--success-bg);
            color: #34d399;
            border-color: rgba(16, 185, 129, 0.3);
        }

        .badge-db-test {
            background: var(--warning-bg);
            color: #fbbf24;
            border-color: rgba(245, 158, 11, 0.3);
        }

        .badge-env-prod {
            background: rgba(37, 99, 235, 0.2);
            color: #60a5fa;
            border-color: rgba(37, 99, 235, 0.4);
        }

        .badge-env-test {
            background: var(--warning-bg);
            color: #fbbf24;
            border-color: rgba(245, 158, 11, 0.3);
        }

        .role-selector {
            background-color: var(--surface-card);
            border: 1px solid var(--surface-border);
            border-radius: 20px;
            padding: 4px 10px;
            color: var(--text-main);
            font-size: 12px;
            font-weight: 600;
            outline: none;
            cursor: pointer;
        }

        /* NAVIGATION TABS */
        .nav-tabs {
            display: flex;
            gap: 8px;
            border-bottom: 1px solid var(--surface-border);
            padding-bottom: 8px;
            overflow-x: auto;
            scrollbar-width: none;
        }

        .nav-tabs::-webkit-scrollbar {
            display: none;
        }

        .nav-tab {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 8px 16px;
            border-radius: var(--radius-md);
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
            display: flex;
            align-items: center;
            gap: 8px;
            white-space: nowrap;
        }

        .nav-tab:hover {
            color: var(--text-main);
            background-color: rgba(255, 255, 255, 0.05);
        }

        .nav-tab.active {
            color: #ffffff;
            background-color: var(--primary);
            box-shadow: 0 4px 12px var(--primary-glow);
        }

        .tab-count {
            background: rgba(255, 255, 255, 0.2);
            padding: 2px 6px;
            border-radius: 10px;
            font-size: 11px;
        }

        /* FUNNEL STATS CARDS */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
        }

        .stat-card {
            background-color: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-lg);
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            cursor: pointer;
            transition: var(--transition);
        }

        .stat-card:hover {
            transform: translateY(-2px);
            border-color: var(--primary);
            background-color: var(--surface-card);
        }

        .stat-card.active-filter {
            border-color: var(--primary);
            box-shadow: 0 0 16px var(--primary-glow);
        }

        .stat-label {
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .stat-value {
            font-size: 24px;
            font-weight: 800;
            color: var(--text-main);
        }

        /* MAIN CARDS */
        .main-card {
            background-color: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-xl);
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
        }

        /* FILTER CONTROLS TOOLBAR */
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }

        .filter-group {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            width: 100%;
            max-width: 900px;
        }

        .input-box, .select-box {
            padding: 8px 12px;
            background-color: var(--surface-card);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-md);
            color: var(--text-main);
            font-size: 13px;
            outline: none;
            transition: var(--transition);
        }

        .input-box:focus, .select-box:focus {
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-glow);
        }

        .search-input {
            flex: 1;
            min-width: 200px;
        }

        .btn {
            background-color: var(--primary);
            color: #ffffff;
            border: none;
            padding: 8px 16px;
            border-radius: var(--radius-md);
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: var(--transition);
        }

        .btn:hover {
            background-color: var(--primary-hover);
            transform: translateY(-1px);
        }

        .btn-secondary {
            background-color: var(--surface-card);
            color: var(--text-main);
            border: 1px solid var(--surface-border);
        }

        .btn-secondary:hover {
            background-color: var(--surface-hover);
        }

        .btn-success {
            background-color: var(--success);
        }

        .btn-danger {
            background-color: var(--danger);
        }

        /* TABLE */
        .table-wrap {
            overflow-x: auto;
            border-radius: var(--radius-lg);
            border: 1px solid var(--surface-border);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }

        th {
            background-color: #172138;
            color: var(--text-muted);
            font-weight: 600;
            padding: 12px 14px;
            border-bottom: 1px solid var(--surface-border);
            white-space: nowrap;
        }

        td {
            padding: 12px 14px;
            border-bottom: 1px solid var(--surface-border);
            vertical-align: middle;
        }

        tr:last-child td {
            border-bottom: none;
        }

        tbody tr {
            cursor: pointer;
            transition: var(--transition);
        }

        tbody tr:hover {
            background-color: rgba(255, 255, 255, 0.03);
        }

        .badge-status {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }

        .st-new { background: rgba(37, 99, 235, 0.2); color: #60a5fa; }
        .st-work { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
        .st-invite { background: rgba(139, 92, 246, 0.2); color: #c084fc; }
        .st-hired { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .st-refused { background: rgba(239, 68, 68, 0.2); color: #f87171; }
        .st-archive { background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }

        .db-tag {
            font-size: 10px;
            font-weight: 700;
            padding: 2px 5px;
            border-radius: 4px;
            text-transform: uppercase;
        }

        .db-tag-prod { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .db-tag-test { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }

        /* PAGINATION */
        .pagination {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            padding-top: 8px;
        }

        .page-info {
            font-size: 13px;
            color: var(--text-muted);
        }

        .page-btns {
            display: flex;
            gap: 6px;
        }

        /* MODAL */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(4px);
            display: none;
            justify-content: center;
            align-items: center;
            padding: 16px;
            z-index: 1000;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal-content {
            background-color: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-xl);
            max-width: 750px;
            width: 100%;
            max-height: 90vh;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 18px;
            padding: 24px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
            animation: modalFadeIn 0.25s ease;
        }

        @keyframes modalFadeIn {
            from { opacity: 0; transform: scale(0.96); }
            to { opacity: 1; transform: scale(1); }
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--surface-border);
            padding-bottom: 12px;
        }

        .modal-title {
            font-size: 18px;
            font-weight: 700;
        }

        .close-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 22px;
            cursor: pointer;
            transition: var(--transition);
        }

        .close-btn:hover {
            color: var(--text-main);
        }

        .detail-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
        }

        .detail-item {
            background-color: var(--surface-card);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-md);
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .detail-label {
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
        }

        .detail-val {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-main);
            word-break: break-word;
        }

        .compliance-box {
            border-radius: var(--radius-md);
            padding: 12px 14px;
            font-size: 13px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .compliance-safe {
            background: var(--success-bg);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: #34d399;
        }

        .compliance-risk {
            background: var(--danger-bg);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #f87171;
        }

        .note-editor {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .note-textarea {
            width: 100%;
            height: 70px;
            background-color: var(--surface-card);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-md);
            color: var(--text-main);
            padding: 10px;
            font-size: 13px;
            outline: none;
            resize: vertical;
        }

        .modal-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            border-top: 1px solid var(--surface-border);
            padding-top: 16px;
            justify-content: flex-end;
        }

        /* TECH & SYSTEM TAB */
        .system-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
        }

        .card-metric {
            background-color: var(--surface-card);
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-lg);
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .action-row {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            padding-top: 10px;
        }

        /* TOAST NOTIFICATION */
        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background-color: #1e293b;
            color: #ffffff;
            border: 1px solid var(--surface-border);
            border-radius: var(--radius-md);
            padding: 12px 20px;
            font-size: 13px;
            font-weight: 600;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            display: none;
            z-index: 2000;
            animation: toastIn 0.2s ease;
        }

        @keyframes toastIn {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        @media (max-width: 768px) {
            body { padding: 10px; }
            .header { padding: 14px; }
            .brand-title { font-size: 17px; }
            .search-input { width: 100%; }
            .filter-group { width: 100%; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- HEADER -->
        <header class="header">
            <div class="header-top">
                <div class="brand">
                    <span class="brand-logo">🚋</span>
                    <div>
                        <div class="brand-title">МУП «Ульяновскэлектротранс»</div>
                        <div class="brand-subtitle">Кабинет управления кадрами и мониторинга</div>
                    </div>
                </div>
                <div class="header-controls">
                    <span id="badgeDb" class="badge-btn badge-db-prod" onclick="toggleActiveDatabase()" title="Кликните для переключения базы">БД: resumes.db ⇄</span>
                    <span id="badgeEnv" class="badge-btn badge-env-test" onclick="toggleEnvironment()" title="Кликните для переключения среды">Среда: TEST ⇄</span>
                    <select id="roleSelector" class="role-selector" onchange="onRoleChange()">
                        <option value="super">👑 SuperAdmin</option>
                        <option value="hr">👔 HR-специалист</option>
                        <option value="tech">🛠 Инженер</option>
                    </select>
                </div>
            </div>

            <!-- TABS -->
            <nav class="nav-tabs">
                <button class="nav-tab active" onclick="switchTab('candidates')">
                    📋 Соискатели <span id="countCandidates" class="tab-count">0</span>
                </button>
                <button class="nav-tab" onclick="switchTab('inquiries')">
                    💬 Вопросы (/ask) <span id="countInquiries" class="tab-count">0</span>
                </button>
                <button class="nav-tab" id="tabTechBtn" onclick="switchTab('tech')">
                    🛠 Панель инженера
                </button>
                <button class="nav-tab" id="tabAdminsBtn" onclick="switchTab('admins')">
                    👥 Администраторы
                </button>
            </nav>
        </header>

        <!-- TAB: CANDIDATES -->
        <section id="tabCandidates" class="main-card">
            <!-- FUNNEL STATS -->
            <div class="stats-grid">
                <div class="stat-card" onclick="filterByStatus('all')">
                    <span class="stat-label">Всего анкет</span>
                    <span id="statTotal" class="stat-value">...</span>
                </div>
                <div class="stat-card" onclick="filterByStatus('Новая')">
                    <span class="stat-label">Новые</span>
                    <span id="statNew" class="stat-value" style="color: #60a5fa;">...</span>
                </div>
                <div class="stat-card" onclick="filterByStatus('В работе')">
                    <span class="stat-label">В работе</span>
                    <span id="statInProgress" class="stat-value" style="color: #fbbf24;">...</span>
                </div>
                <div class="stat-card" onclick="filterByStatus('Приглашен')">
                    <span class="stat-label">Собеседование</span>
                    <span id="statInvited" class="stat-value" style="color: #c084fc;">...</span>
                </div>
                <div class="stat-card" onclick="filterByStatus('Принят')">
                    <span class="stat-label">Приняты в штат</span>
                    <span id="statHired" class="stat-value" style="color: #34d399;">...</span>
                </div>
                <div class="stat-card" onclick="filterByStatus('Отказ')">
                    <span class="stat-label">Отказ / ЧС</span>
                    <span id="statRejected" class="stat-value" style="color: #f87171;">...</span>
                </div>
                <div class="stat-card" onclick="filterByStatus('Архив')">
                    <span class="stat-label">В архиве</span>
                    <span id="statArchive" class="stat-value" style="color: #94a3b8;">...</span>
                </div>
            </div>

            <!-- TOOLBAR -->
            <div class="toolbar">
                <div class="filter-group">
                    <input type="text" id="searchInput" class="input-box search-input" placeholder="🔍 Поиск по ФИО, телефону, вакансии или #ID...">
                    <select id="dbScopeSelect" class="select-box" onchange="loadCandidates(1)">
                        <option value="both">🌐 Все базы (Боевая + Тест)</option>
                        <option value="prod">📁 Боевая (resumes.db)</option>
                        <option value="test">🧪 Тестовая (resumes_test.db)</option>
                    </select>
                    <select id="statusFilterSelect" class="select-box" onchange="loadCandidates(1)">
                        <option value="all">Все статусы</option>
                        <option value="Новая">Новая</option>
                        <option value="В работе">В работе</option>
                        <option value="Приглашен">Приглашен</option>
                        <option value="Принят">Принят</option>
                        <option value="Отказ">Отказ</option>
                        <option value="Архив">Архив</option>
                    </select>
                    <select id="sortSelect" class="select-box" onchange="loadCandidates(1)">
                        <option value="desc">Сначала новые (#)</option>
                        <option value="asc">Сначала старые (#)</option>
                    </select>
                </div>
                <div style="display: flex; gap: 8px;">
                    <a href="/api/system/export_csv" class="btn btn-secondary" title="Экспорт в Excel">📥 Экспорт Excel</a>
                    <button class="btn" onclick="loadCandidates(1)">🔄 Обновить</button>
                </div>
            </div>

            <!-- TABLE -->
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>№ ID</th>
                            <th>База</th>
                            <th>Дата подачи</th>
                            <th>ФИО соискателя</th>
                            <th>Вакансия</th>
                            <th>Телефон</th>
                            <th>Права</th>
                            <th>Статус</th>
                            <th>Депо / Время</th>
                            <th>Действие</th>
                        </tr>
                    </thead>
                    <tbody id="candidatesTbody">
                        <tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Загрузка данных...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- PAGINATION -->
            <div class="pagination">
                <div id="pageInfo" class="page-info">Показано 0 из 0 анкет</div>
                <div class="page-btns">
                    <button id="btnPrevPage" class="btn btn-secondary" onclick="changePage(-1)">← Назад</button>
                    <button id="btnNextPage" class="btn btn-secondary" onclick="changePage(1)">Вперед →</button>
                </div>
            </div>
        </section>

        <!-- TAB: INQUIRIES -->
        <section id="tabInquiries" class="main-card" style="display: none;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h3>💬 Вопросы соискателей (/ask)</h3>
                <button class="btn" onclick="loadInquiries()">🔄 Обновить</button>
            </div>
            <div id="inquiriesList" style="display: flex; flex-direction: column; gap: 12px;">
                <div style="text-align: center; color: var(--text-muted); padding: 24px;">Загрузка вопросов...</div>
            </div>
        </section>

        <!-- TAB: TECH -->
        <section id="tabTech" class="main-card" style="display: none;">
            <h3>🛠 Панель инженера и системный мониторинг</h3>
            <div class="system-grid">
                <div class="card-metric">
                    <span class="detail-label">Потребление ОЗУ (Процесс)</span>
                    <span id="metricRam" class="stat-value">...</span>
                </div>
                <div class="card-metric">
                    <span class="detail-label">Системная память</span>
                    <span id="metricSysRam" class="stat-value">...</span>
                </div>
                <div class="card-metric">
                    <span class="detail-label">Аптайм сервиса</span>
                    <span id="metricUptime" class="stat-value">...</span>
                </div>
                <div class="card-metric">
                    <span class="detail-label">Размер боевой БД</span>
                    <span id="metricDbProd" class="stat-value">...</span>
                </div>
                <div class="card-metric">
                    <span class="detail-label">Размер тестовой БД</span>
                    <span id="metricDbTest" class="stat-value">...</span>
                </div>
                <div class="card-metric">
                    <span class="detail-label">Шлюзы</span>
                    <span id="metricGateways" style="font-size: 14px; font-weight: 600; color: #34d399;">TG (OK) | VK (OK) | WEB (OK)</span>
                </div>
            </div>

            <h4 style="margin-top: 14px;">⚡ Действия технического обслуживания</h4>
            <div class="action-row">
                <button class="btn btn-secondary" onclick="toggleActiveDatabase()">🔄 Переключить БД (Боевая ⇄ Тест)</button>
                <button class="btn btn-secondary" onclick="toggleEnvironment()">⚙️ Сменить режим (TEST ⇄ PROD)</button>
                <button class="btn btn-secondary" onclick="createBackup()">💾 Создать резервную копию (Бэкап)</button>
                <button class="btn btn-secondary" onclick="optimizeDatabase()">🧹 Оптимизировать БД (VACUUM & Checkpoint)</button>
                <button class="btn btn-secondary" onclick="muteAllHr()">🔕 Выключить уведомления в ЛС всем HR</button>
            </div>
        </section>

        <!-- TAB: ADMINS -->
        <section id="tabAdmins" class="main-card" style="display: none;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h3>👥 Управление кадровиками и администраторами</h3>
                <button class="btn" onclick="loadAdmins()">🔄 Обновить</button>
            </div>

            <div style="background-color: var(--surface-card); padding: 16px; border-radius: var(--radius-lg); border: 1px solid var(--surface-border); display: flex; gap: 10px; flex-wrap: wrap;">
                <input type="text" id="newAdminId" class="input-box" placeholder="Telegram ID пользователя...">
                <select id="newAdminRole" class="select-box">
                    <option value="hr">HR-специалист</option>
                    <option value="tech">Инженер (Tech)</option>
                </select>
                <button class="btn" onclick="addNewAdmin()">➕ Назначить права</button>
            </div>

            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Telegram ID</th>
                            <th>Роль</th>
                            <th>Уведомления в ЛС</th>
                            <th>Дата назначения</th>
                            <th>Действие</th>
                        </tr>
                    </thead>
                    <tbody id="adminsTbody">
                        <tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 20px;">Загрузка...</td></tr>
                    </tbody>
                </table>
            </div>
        </section>
    </div>

    <!-- CANDIDATE DETAIL MODAL -->
    <div id="candidateModal" class="modal-overlay" onclick="onModalOverlayClick(event)">
        <div class="modal-content">
            <div class="modal-header">
                <div>
                    <h3 id="modalCandTitle" class="modal-title">Анкета соискателя #...</h3>
                    <div id="modalCandSub" style="font-size: 12px; color: var(--text-muted);">...</div>
                </div>
                <button class="close-btn" onclick="closeModal()">×</button>
            </div>

            <!-- COMPLIANCE ALERT -->
            <div id="modalComplianceBox" class="compliance-box compliance-safe">
                <b>🛡 Проверка безопасности:</b>
                <span id="modalComplianceText">...</span>
            </div>

            <!-- DETAILS GRID -->
            <div class="detail-grid">
                <div class="detail-item">
                    <span class="detail-label">ФИО</span>
                    <span id="mFio" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Телефон</span>
                    <span id="mPhone" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Вакансия</span>
                    <span id="mVacancy" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Опыт работы</span>
                    <span id="mExp" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Водительские категории</span>
                    <span id="mLicense" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Дата рождения / Возраст</span>
                    <span id="mBirth" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Город проживания</span>
                    <span id="mCity" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Образование</span>
                    <span id="mEdu" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Сменный график / Общежитие</span>
                    <span id="mConditions" class="detail-val">...</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Собеседование (Депо и время)</span>
                    <span id="mInterview" class="detail-val">...</span>
                </div>
            </div>

            <!-- NOTE EDITOR -->
            <div class="note-editor">
                <span class="detail-label">Заметка кадровой службы:</span>
                <textarea id="mAdminNote" class="note-textarea" placeholder="Введите внутренний комментарий..."></textarea>
                <div style="display: flex; justify-content: flex-end;">
                    <button class="btn btn-secondary" onclick="saveCandidateNote()">💾 Сохранить заметку</button>
                </div>
            </div>

            <!-- STATUS ACTIONS -->
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="changeCandidateStatus('В работе')">🟡 В работу</button>
                <button class="btn btn-secondary" onclick="changeCandidateStatus('Приглашен')">📅 Пригласить на собеседование</button>
                <button class="btn btn-success" onclick="changeCandidateStatus('Принят')">🟢 Принят в депо</button>
                <button class="btn btn-danger" onclick="changeCandidateStatus('Отказ')">🔴 Отказ</button>
                <button class="btn btn-secondary" onclick="changeCandidateStatus('Архив')">📦 В архив</button>
            </div>
        </div>
    </div>

    <!-- TOAST -->
    <div id="toast" class="toast">Уведомление</div>

    <script>
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
        }

        let currentPage = 1;
        let totalPages = 1;
        let currentStatusFilter = 'all';
        let activeTicketId = null;

        function showToast(msg) {
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.style.display = 'block';
            setTimeout(() => { t.style.display = 'none'; }, 3000);
        }

        function switchTab(tab) {
            document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
            ['tabCandidates', 'tabInquiries', 'tabTech', 'tabAdmins'].forEach(id => {
                document.getElementById(id).style.display = 'none';
            });

            if (tab === 'candidates') {
                document.getElementById('tabCandidates').style.display = 'flex';
                document.querySelectorAll('.nav-tab')[0].classList.add('active');
                loadCandidates(currentPage);
            } else if (tab === 'inquiries') {
                document.getElementById('tabInquiries').style.display = 'flex';
                document.querySelectorAll('.nav-tab')[1].classList.add('active');
                loadInquiries();
            } else if (tab === 'tech') {
                document.getElementById('tabTech').style.display = 'flex';
                document.getElementById('tabTechBtn').classList.add('active');
                loadTechMetrics();
            } else if (tab === 'admins') {
                document.getElementById('tabAdmins').style.display = 'flex';
                document.getElementById('tabAdminsBtn').classList.add('active');
                loadAdmins();
            }
        }

        function onRoleChange() {
            const role = document.getElementById('roleSelector').value;
            const techBtn = document.getElementById('tabTechBtn');
            const admBtn = document.getElementById('tabAdminsBtn');
            if (role === 'hr') {
                techBtn.style.display = 'none';
                admBtn.style.display = 'none';
                switchTab('candidates');
            } else {
                techBtn.style.display = 'flex';
                admBtn.style.display = 'flex';
            }
            showToast("Переключен режим: " + role.toUpperCase());
        }

        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                if (!res.ok) return;
                const d = await res.json();
                document.getElementById('statTotal').innerText = d.total || 0;
                document.getElementById('statNew').innerText = d.new || 0;
                document.getElementById('statInProgress').innerText = d.in_progress || 0;
                document.getElementById('statInvited').innerText = d.invited || 0;
                document.getElementById('statHired').innerText = d.hired || 0;
                document.getElementById('statRejected').innerText = d.rejected || 0;
                document.getElementById('statArchive').innerText = d.archive || 0;
                document.getElementById('countCandidates').innerText = d.total || 0;
                document.getElementById('countInquiries').innerText = d.inquiries || 0;

                const bDb = document.getElementById('badgeDb');
                bDb.innerText = 'БД: ' + (d.active_db || 'resumes.db') + ' ⇄';
                bDb.className = (d.active_db === 'resumes_test.db') ? 'badge-btn badge-db-test' : 'badge-btn badge-db-prod';

                const bEnv = document.getElementById('badgeEnv');
                bEnv.innerText = 'Среда: ' + (d.env || 'TEST') + ' ⇄';
                bEnv.className = (d.env === 'PROD') ? 'badge-btn badge-env-prod' : 'badge-btn badge-env-test';
            } catch (e) {
                console.error(e);
            }
        }

        async function loadCandidates(page = 1) {
            currentPage = page;
            const query = document.getElementById('searchInput').value.trim();
            const dbScope = document.getElementById('dbScopeSelect').value;
            const sort = document.getElementById('sortSelect').value;
            const status = document.getElementById('statusFilterSelect').value;

            const url = `/api/candidates?page=${page}&limit=20&status=${encodeURIComponent(status)}&db_scope=${dbScope}&search=${encodeURIComponent(query)}&sort=${sort}`;
            try {
                const res = await fetch(url);
                if (!res.ok) return;
                const data = await res.json();
                totalPages = data.total_pages || 1;

                document.getElementById('pageInfo').innerText = `Показано ${data.candidates.length} из ${data.total} анкет (Стр. ${data.page} из ${totalPages})`;
                document.getElementById('btnPrevPage').disabled = (data.page <= 1);
                document.getElementById('btnNextPage').disabled = (data.page >= totalPages);

                renderCandidatesTable(data.candidates);
                loadStats();
            } catch (e) {
                console.error(e);
            }
        }

        function renderCandidatesTable(list) {
            const tbody = document.getElementById('candidatesTbody');
            if (!list || list.length === 0) {
                tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Анкеты не найдены</td></tr>';
                return;
            }

            let html = '';
            list.forEach(c => {
                let stBadge = 'st-new';
                const st = (c.status || '').toLowerCase();
                if (st.includes('работ')) stBadge = 'st-work';
                else if (st.includes('приглаш')) stBadge = 'st-invite';
                else if (st.includes('принят')) stBadge = 'st-hired';
                else if (st.includes('отказ')) stBadge = 'st-refused';
                else if (st.includes('архив')) stBadge = 'st-archive';

                const dbBadge = (c.is_test || c.ticket_id >= 900000)
                    ? '<span class="db-tag db-tag-test">TEST</span>'
                    : '<span class="db-tag db-tag-prod">PROD</span>';

                const depotStr = (c.interview_depot || '') + (c.interview_time ? ' (' + c.interview_time + ')' : '');

                html += `<tr onclick="openCandidateModal(${c.ticket_id})">
                    <td><b>#${c.ticket_id}</b></td>
                    <td>${dbBadge}</td>
                    <td style="color: var(--text-muted);">${c.created_at || '—'}</td>
                    <td><b>${escapeHtml(c.full_name || 'Не указано')}</b></td>
                    <td>${escapeHtml(c.vacancy || '—')}</td>
                    <td><code>${escapeHtml(c.phone || '—')}</code></td>
                    <td><span style="font-size: 11px;">${escapeHtml(c.driver_license || '—')}</span></td>
                    <td><span class="badge-status ${stBadge}">${escapeHtml(c.status || 'Новая')}</span></td>
                    <td style="color: var(--text-muted); font-size: 12px;">${escapeHtml(depotStr || '—')}</td>
                    <td><button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;" onclick="event.stopPropagation(); openCandidateModal(${c.ticket_id})">Открыть</button></td>
                </tr>`;
            });
            tbody.innerHTML = html;
        }

        async function openCandidateModal(ticketId) {
            activeTicketId = ticketId;
            try {
                const res = await fetch(`/api/candidate/${ticketId}`);
                if (!res.ok) return;
                const c = await res.json();

                document.getElementById('modalCandTitle').innerText = `Анкета #${c.ticket_id} — ${c.full_name || 'Соискатель'}`;
                document.getElementById('modalCandSub').innerText = `Статус: ${c.status} | Подано: ${c.created_at} [${(c.platform || 'tg').toUpperCase()}]`;

                document.getElementById('mFio').innerText = c.full_name || '—';
                document.getElementById('mPhone').innerHTML = `<a href="tel:${c.phone}" style="color: #60a5fa; text-decoration: none;">${c.phone}</a>`;
                document.getElementById('mVacancy').innerText = c.vacancy || '—';
                document.getElementById('mExp').innerText = c.experience || '—';
                document.getElementById('mLicense').innerText = c.driver_license || 'Отсутствует';
                document.getElementById('mBirth').innerText = c.birth_date || '—';
                document.getElementById('mCity').innerText = c.city || '—';
                document.getElementById('mEdu').innerText = c.education || '—';
                document.getElementById('mConditions').innerText = `Смены: ${c.shift_work || '—'} | Общежитие: ${c.dormitory || '—'}`;
                document.getElementById('mInterview').innerText = (c.interview_depot || '—') + (c.interview_time ? ' (' + c.interview_time + ')' : '');

                document.getElementById('mAdminNote').value = c.admin_note || '';

                const compBox = document.getElementById('modalComplianceBox');
                const compText = document.getElementById('modalComplianceText');
                if (c.compliance && c.compliance.has_risk) {
                    compBox.className = 'compliance-box compliance-risk';
                    compText.innerHTML = c.compliance.text;
                } else {
                    compBox.className = 'compliance-box compliance-safe';
                    compText.innerHTML = 'Соответствует требованиям законодательства (ст. 328.1 ТК РФ и ст. 86 УК РФ).';
                }

                document.getElementById('candidateModal').classList.add('active');
            } catch (e) {
                console.error(e);
            }
        }

        function closeModal() {
            document.getElementById('candidateModal').classList.remove('active');
            activeTicketId = null;
        }

        function onModalOverlayClick(e) {
            if (e.target.id === 'candidateModal') closeModal();
        }

        async function saveCandidateNote() {
            if (!activeTicketId) return;
            const note = document.getElementById('mAdminNote').value;
            const res = await fetch(`/api/candidate/${activeTicketId}/note`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note })
            });
            if (res.ok) {
                showToast("Заметка успешно сохранена!");
            }
        }

        async function changeCandidateStatus(newStatus) {
            if (!activeTicketId) return;
            const res = await fetch(`/api/candidate/${activeTicketId}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: newStatus })
            });
            if (res.ok) {
                showToast(`Статус изменен на: ${newStatus}`);
                closeModal();
                loadCandidates(currentPage);
            }
        }

        function filterByStatus(st) {
            currentStatusFilter = st;
            document.getElementById('statusFilterSelect').value = st;
            loadCandidates(1);
        }

        function changePage(delta) {
            const next = currentPage + delta;
            if (next >= 1 && next <= totalPages) {
                loadCandidates(next);
            }
        }

        async function toggleActiveDatabase() {
            if (!confirm("Переключить активную базу данных предприятия?")) return;
            const res = await fetch('/api/system/toggle_db', { method: 'POST' });
            if (res.ok) {
                const d = await res.json();
                showToast("Активная БД: " + d.active_db);
                loadCandidates(1);
            }
        }

        async function toggleEnvironment() {
            if (!confirm("Сменить режим окружения (TEST / PROD)?")) return;
            const res = await fetch('/api/system/toggle_env', { method: 'POST' });
            if (res.ok) {
                const d = await res.json();
                showToast("Окружение: " + d.env);
                loadCandidates(1);
            }
        }

        async function createBackup() {
            showToast("Создание бэкапа...");
            const res = await fetch('/api/system/backup', { method: 'POST' });
            if (res.ok) {
                const d = await res.json();
                showToast("Бэкап создан: " + d.filename);
            }
        }

        async function optimizeDatabase() {
            showToast("Выполняется VACUUM и WAL Checkpoint...");
            const res = await fetch('/api/system/optimize_db', { method: 'POST' });
            if (res.ok) {
                showToast("Базы данных успешно оптимизированы!");
            }
        }

        async function muteAllHr() {
            if (!confirm("Отключить уведомления в ЛС всем сотрудникам отдела кадров?")) return;
            const res = await fetch('/api/admins/mute_all', { method: 'POST' });
            if (res.ok) {
                showToast("Уведомления в ЛС выключены для всех HR!");
            }
        }

        async function loadTechMetrics() {
            try {
                const res = await fetch('/api/system');
                if (!res.ok) return;
                const d = await res.json();
                document.getElementById('metricRam').innerText = `${d.process_rss_mb} МБ (${d.process_ram_pct}%)`;
                document.getElementById('metricSysRam').innerText = `${d.system_ram_used_gb} / ${d.system_ram_total_gb} ГБ`;
                document.getElementById('metricUptime').innerText = d.uptime || '—';
                document.getElementById('metricDbProd').innerText = `${d.db_prod_kb} КБ (${d.db_prod_rows} анкет)`;
                document.getElementById('metricDbTest').innerText = `${d.db_test_kb} КБ (${d.db_test_rows} анкет)`;
            } catch (e) {
                console.error(e);
            }
        }

        async function loadInquiries() {
            const listEl = document.getElementById('inquiriesList');
            try {
                const res = await fetch('/api/inquiries');
                if (!res.ok) return;
                const items = await res.json();
                if (!items || items.length === 0) {
                    listEl.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых обращений</div>';
                    return;
                }
                let html = '';
                items.forEach(inq => {
                    html += `<div style="background-color: var(--surface-card); border: 1px solid var(--surface-border); border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 8px;">
                        <div style="display: flex; justify-content: space-between;">
                            <b>#${inq.inquiry_id} | ${escapeHtml(inq.full_name || 'Соискатель')} (${escapeHtml(inq.phone || '—')})</b>
                            <span class="badge-status st-work">${escapeHtml(inq.status || 'Новое')}</span>
                        </div>
                        <div style="font-size: 13px; color: var(--text-main); background: rgba(0,0,0,0.2); padding: 8px 12px; border-radius: 6px;">
                            ${escapeHtml(inq.question_text || '')}
                        </div>
                        <div style="display: flex; gap: 8px; margin-top: 4px;">
                            <input type="text" id="replyInq_${inq.inquiry_id}" class="input-box" style="flex: 1;" placeholder="Ответ соискателю в бот...">
                            <button class="btn" onclick="sendInquiryReply(${inq.inquiry_id})">Отправить ответ</button>
                        </div>
                    </div>`;
                });
                listEl.innerHTML = html;
            } catch (e) {
                console.error(e);
            }
        }

        async function sendInquiryReply(inquiryId) {
            const inp = document.getElementById(`replyInq_${inquiryId}`);
            const text = (inp.value || '').trim();
            if (!text) return;
            const res = await fetch(`/api/inquiries/${inquiryId}/reply`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reply: text })
            });
            if (res.ok) {
                showToast("Ответ успешно отправлен соискателю!");
                loadInquiries();
            }
        }

        async function loadAdmins() {
            try {
                const res = await fetch('/api/admins');
                if (!res.ok) return;
                const admins = await res.json();
                const tbody = document.getElementById('adminsTbody');
                if (!admins || admins.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 16px;">Список пуст</td></tr>';
                    return;
                }
                let html = '';
                admins.forEach(a => {
                    const notifyBadge = a.notify_dm ? '<span style="color: #34d399;">🔔 Включено</span>' : '<span style="color: #f87171;">🔕 Выключено</span>';
                    html += `<tr>
                        <td><b><code>${a.user_id}</code></b></td>
                        <td><b>${a.role.toUpperCase()}</b></td>
                        <td>${notifyBadge}</td>
                        <td style="color: var(--text-muted);">${a.added_at || '—'}</td>
                        <td><button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="removeAdmin(${a.user_id})">Отозвать</button></td>
                    </tr>`;
                });
                tbody.innerHTML = html;
            } catch (e) {
                console.error(e);
            }
        }

        async function addNewAdmin() {
            const uid = document.getElementById('newAdminId').value.trim();
            const role = document.getElementById('newAdminRole').value;
            if (!uid) return;
            const res = await fetch('/api/admins/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: uid, role })
            });
            if (res.ok) {
                showToast("Администратор назначен!");
                document.getElementById('newAdminId').value = '';
                loadAdmins();
            }
        }

        async function removeAdmin(uid) {
            if (!confirm(`Отозвать права администратора у ID ${uid}?`)) return;
            const res = await fetch('/api/admins/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: uid })
            });
            if (res.ok) {
                showToast("Права отозваны!");
                loadAdmins();
            }
        }

        function escapeHtml(text) {
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        document.getElementById('searchInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') loadCandidates(1);
        });

        loadStats();
        loadCandidates(1);
    </script>
</body>
</html>
"""


class WebPanel:
    def __init__(self, db: ResumeDB, bot: Optional[Bot] = None):
        self.db = db
        self.bot = bot
        self.start_time = datetime.now()
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/app", self.handle_index)

        # Candidates API
        self.app.router.add_get("/api/stats", self.handle_api_stats)
        self.app.router.add_get("/api/candidates", self.handle_api_candidates)
        self.app.router.add_get("/api/candidate/{ticket_id}", self.handle_api_candidate_detail)
        self.app.router.add_post("/api/candidate/{ticket_id}/status", self.handle_api_candidate_status)
        self.app.router.add_post("/api/candidate/{ticket_id}/note", self.handle_api_candidate_note)

        # Inquiries API
        self.app.router.add_get("/api/inquiries", self.handle_api_inquiries)
        self.app.router.add_post("/api/inquiries/{inquiry_id}/reply", self.handle_api_inquiry_reply)

        # System & Tech API
        self.app.router.add_get("/api/system", self.handle_api_system)
        self.app.router.add_post("/api/system/toggle_db", self.handle_api_toggle_db)
        self.app.router.add_post("/api/system/toggle_env", self.handle_api_toggle_env)
        self.app.router.add_post("/api/system/backup", self.handle_api_backup)
        self.app.router.add_post("/api/system/optimize_db", self.handle_api_optimize_db)
        self.app.router.add_get("/api/system/export_csv", self.handle_api_export_csv)

        # Admins Management API
        self.app.router.add_get("/api/admins", self.handle_api_admins)
        self.app.router.add_post("/api/admins/add", self.handle_api_admins_add)
        self.app.router.add_post("/api/admins/remove", self.handle_api_admins_remove)
        self.app.router.add_post("/api/admins/mute_all", self.handle_api_admins_mute_all)

        self.app.router.add_get("/api/health", self.handle_api_health)

    async def handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=HTML_TEMPLATE, content_type="text/html", charset="utf-8")

    async def handle_api_stats(self, request: web.Request) -> web.Response:
        env = CONFIG.get("ENVIRONMENT", "TEST").upper()
        active_db = self.db.get_setting("active_db_target", "resumes.db")

        try:
            stats = self.db.get_statistics()
            inq_cnt = 0
            with self.db.connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM inquiries WHERE status != 'closed'")
                inq_cnt = cur.fetchone()[0]

            return web.json_response({
                "env": env,
                "active_db": active_db,
                "total": stats.get("total", 0),
                "new": stats.get("new", 0),
                "in_progress": stats.get("in_progress", 0),
                "invited": stats.get("invited", 0),
                "hired": stats.get("hired", 0),
                "rejected": stats.get("rejected", 0),
                "archive": stats.get("archive", 0),
                "inquiries": inq_cnt,
            })
        except Exception as e:
            logger.error("Ошибка API stats: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_candidates(self, request: web.Request) -> web.Response:
        """
        Возвращает пагинированный список кандидатов с фильтрацией по статусу,
        базе (обе базы, только боевая или только тест) и поисковому запросу.
        """
        try:
            page = max(1, int(request.query.get("page", 1)))
            limit = max(1, min(100, int(request.query.get("limit", 25))))
            offset = (page - 1) * limit
            status_filter = request.query.get("status", "all")
            db_scope = request.query.get("db_scope", "both")
            search = (request.query.get("search", "")).strip().lower()
            sort_order = "ASC" if request.query.get("sort", "desc") == "asc" else "DESC"

            active_db = self.db.get_setting("active_db_target", "resumes.db")

            target_dbs: List[Tuple[str, bool]] = []
            if db_scope == "both":
                target_dbs = [("prod", False), ("test", True)]
            elif db_scope == "prod":
                target_dbs = [("prod", False)]
            elif db_scope == "test":
                target_dbs = [("test", True)]
            else:
                target_dbs = [("test" if active_db == "resumes_test.db" else "prod", active_db == "resumes_test.db")]

            all_rows = []
            for scope_name, is_test in target_dbs:
                with self.db.connection(is_test=is_test) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT ticket_id, created_at, full_name, vacancy, phone, status,
                               interview_depot, interview_time, driver_license, is_test
                        FROM candidates
                        """
                    )
                    rows = cursor.fetchall()
                    for r in rows:
                        t_id, cr, fn, vac, ph, st, depot, itime, lic, test_flag = r
                        row_dict = {
                            "ticket_id": t_id,
                            "created_at": cr or "",
                            "full_name": fn or "",
                            "vacancy": vac or "",
                            "phone": ph or "",
                            "status": st or "Новая",
                            "interview_depot": depot or "",
                            "interview_time": itime or "",
                            "driver_license": lic or "",
                            "is_test": bool(test_flag or is_test or t_id >= 900000),
                            "db_scope": scope_name,
                        }
                        all_rows.append(row_dict)

            # Фильтрация по статусу
            if status_filter and status_filter != "all":
                all_rows = [c for c in all_rows if c["status"] == status_filter or (status_filter in c["status"])]

            # Поиск по ФИО, телефону, вакансии, номеру
            if search:
                all_rows = [
                    c for c in all_rows
                    if search in c["full_name"].lower()
                    or search in c["phone"].lower()
                    or search in c["vacancy"].lower()
                    or search in str(c["ticket_id"])
                ]

            # Сортировка
            all_rows.sort(key=lambda x: x["ticket_id"], reverse=(sort_order == "DESC"))

            total_items = len(all_rows)
            total_pages = max(1, (total_items + limit - 1) // limit)
            paginated = all_rows[offset : offset + limit]

            return web.json_response({
                "candidates": paginated,
                "total": total_items,
                "page": page,
                "total_pages": total_pages,
                "active_db": active_db,
                "db_scope": db_scope,
            })
        except Exception as e:
            logger.error("Ошибка API candidates: %s", e)
            return web.json_response({"candidates": [], "total": 0, "page": 1, "total_pages": 1}, status=500)

    async def handle_api_candidate_detail(self, request: web.Request) -> web.Response:
        """Детальная информация о кандидате со скорингом безопасности."""
        ticket_id = int(request.match_info["ticket_id"])
        cand = self.db.get_candidate(ticket_id)
        if not cand:
            return web.json_response({"error": "Кандидат не найден"}, status=404)

        (
            t_id, plat, uid, fn, ph, vac, exp, st, note, cr,
            bdate, city, lic, edu, reloc, dorm, shift,
            med, crim, src, extra, consent_ts, raw_json,
            depot, itime, istatus
        ) = cand

        # Оценка комплаенс-контроля (ст. 328.1 ТК РФ)
        has_risk, risk_desc = ComplianceService.check_criminal_record_risk(vac, crim)

        return web.json_response({
            "ticket_id": t_id,
            "platform": plat,
            "user_id": uid,
            "full_name": fn,
            "phone": ph,
            "vacancy": vac,
            "experience": exp,
            "status": st,
            "admin_note": note or "",
            "created_at": cr,
            "birth_date": bdate or "",
            "city": city or "",
            "driver_license": lic or "",
            "education": edu or "",
            "relocation": reloc or "",
            "dormitory": dorm or "",
            "shift_work": shift or "",
            "medical_restrictions": med or "",
            "criminal_record": crim or "",
            "source": src or "",
            "extra_info": extra or "",
            "interview_depot": depot or "",
            "interview_time": itime or "",
            "interview_status": istatus or "pending",
            "compliance": {
                "has_risk": has_risk,
                "text": risk_desc,
            }
        })

    async def handle_api_candidate_status(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        data = await request.json()
        new_status = data.get("status")
        if not new_status:
            return web.json_response({"error": "Status required"}, status=400)

        self.db.update_status(ticket_id, new_status)
        logger.info("Веб-панель: статус анкеты #%s изменен на %s", ticket_id, new_status)
        return web.json_response({"ok": True, "ticket_id": ticket_id, "status": new_status})

    async def handle_api_candidate_note(self, request: web.Request) -> web.Response:
        ticket_id = int(request.match_info["ticket_id"])
        data = await request.json()
        note = data.get("note", "")

        self.db.update_admin_note(ticket_id, note)
        logger.info("Веб-панель: заметка к анкете #%s сохранена", ticket_id)
        return web.json_response({"ok": True, "ticket_id": ticket_id})

    async def handle_api_inquiries(self, request: web.Request) -> web.Response:
        """Список обращений кандидатов."""
        inquiries = []
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT inquiry_id, ticket_id, platform, user_id, full_name, phone, vacancy,
                       question_text, status, created_at
                FROM inquiries
                ORDER BY inquiry_id DESC
                LIMIT 50
                """
            )
            for r in cur.fetchall():
                inquiries.append({
                    "inquiry_id": r[0],
                    "ticket_id": r[1],
                    "platform": r[2],
                    "user_id": r[3],
                    "full_name": r[4] or "",
                    "phone": r[5] or "",
                    "vacancy": r[6] or "",
                    "question_text": r[7] or "",
                    "status": r[8] or "Новое",
                    "created_at": r[9] or "",
                })
        return web.json_response(inquiries)

    async def handle_api_inquiry_reply(self, request: web.Request) -> web.Response:
        inquiry_id = int(request.match_info["inquiry_id"])
        data = await request.json()
        reply_text = data.get("reply", "").strip()
        if not reply_text:
            return web.json_response({"error": "Reply text required"}, status=400)

        inq = self.db.get_inquiry(inquiry_id)
        if not inq:
            return web.json_response({"error": "Inquiry not found"}, status=404)

        user_id = inq[3]
        platform = inq[2]

        self.db.reply_inquiry(inquiry_id, reply_text)

        # Отправка соискателю в Telegram
        if self.bot and platform == "tg" and str(user_id).lstrip("-").isdigit():
            try:
                from common import safe_send
                await safe_send(
                    self.bot,
                    int(user_id),
                    f"💬 <b>Ответ специалиста кадров МУП «УЭТ»:</b>\n\n{html.escape(reply_text)}"
                )
            except Exception as e:
                logger.warning("Не удалось переслать ответ соискателю: %s", e)

        return web.json_response({"ok": True, "inquiry_id": inquiry_id})

    async def handle_api_system(self, request: web.Request) -> web.Response:
        """Системные метрики для панели инженера."""
        import os
        uptime_seconds = int((datetime.now() - self.start_time).total_seconds())
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}ч {minutes}м {seconds}с"

        # Метрики памяти процесса и системы через /proc
        rss_mb = 0.0
        sys_ram_used = "—"
        sys_ram_total = "—"
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_mb = round(int(line.split()[1]) / 1024, 1)
                        break
        except Exception:
            pass

        try:
            mem_info = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem_info[parts[0].strip()] = int(parts[1].strip().split()[0])
            total_kb = mem_info.get("MemTotal", 0)
            avail_kb = mem_info.get("MemAvailable", 0)
            if total_kb > 0:
                sys_ram_total = round(total_kb / 1024 / 1024, 2)
                sys_ram_used = round((total_kb - avail_kb) / 1024 / 1024, 2)
        except Exception:
            pass

        # Размеры баз данных
        db_prod_path = Path(self.db.db_path)
        db_test_path = Path(self.db.test_db_path)
        db_prod_kb = round(db_prod_path.stat().st_size / 1024, 1) if db_prod_path.exists() else 0
        db_test_kb = round(db_test_path.stat().st_size / 1024, 1) if db_test_path.exists() else 0

        # Количество записей
        db_prod_rows = 0
        db_test_rows = 0
        with self.db.connection(is_test=False) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM candidates")
            db_prod_rows = cur.fetchone()[0]
        with self.db.connection(is_test=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM candidates")
            db_test_rows = cur.fetchone()[0]

        return web.json_response({
            "uptime": uptime_str,
            "process_rss_mb": rss_mb,
            "process_ram_pct": round(rss_mb / (float(sys_ram_total) * 10.24), 1) if sys_ram_total != "—" and float(sys_ram_total) > 0 else 0,
            "system_ram_used_gb": sys_ram_used,
            "system_ram_total_gb": sys_ram_total,
            "db_prod_kb": db_prod_kb,
            "db_test_kb": db_test_kb,
            "db_prod_rows": db_prod_rows,
            "db_test_rows": db_test_rows,
            "bot_version": CONFIG.get("BOT_VERSION", "1.6.0"),
        })

    async def handle_api_toggle_db(self, request: web.Request) -> web.Response:
        """Переключение активной базы данных."""
        cur_db = self.db.get_setting("active_db_target", "resumes.db")
        new_db = "resumes_test.db" if cur_db == "resumes.db" else "resumes.db"
        self.db.set_setting("active_db_target", new_db)
        logger.info("Веб-панель: активная база переключена на %s", new_db)
        return web.json_response({"ok": True, "active_db": new_db})

    async def handle_api_toggle_env(self, request: web.Request) -> web.Response:
        """Переключение режима окружения TEST / PROD."""
        cur_env = CONFIG.get("ENVIRONMENT", "TEST").upper()
        new_env = "PROD" if cur_env == "TEST" else "TEST"
        CONFIG["ENVIRONMENT"] = new_env
        update_env_variable("ENVIRONMENT", new_env)
        logger.info("Веб-панель: режим окружения переключен на %s", new_env)
        return web.json_response({"ok": True, "env": new_env})

    async def handle_api_backup(self, request: web.Request) -> web.Response:
        """Создание резервной копии базы данных."""
        backup_dir = Path(CONFIG.get("BACKUP_DIR", "/root/uetbot/backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_name = f"web_backup_{ts}.db"
        target_path = backup_dir / target_name

        shutil.copy2(self.db.db_path, target_path)
        logger.info("Веб-панель: бэкап сохранен в %s", target_path)
        return web.json_response({"ok": True, "filename": target_name})

    async def handle_api_optimize_db(self, request: web.Request) -> web.Response:
        """Запуск VACUUM и WAL Checkpoint."""
        ok = self.db.checkpoint_and_optimize()
        return web.json_response({"ok": ok})

    async def handle_api_export_csv(self, request: web.Request) -> web.Response:
        """Выгрузка базы соискателей в формате CSV с UTF-8 BOM для Microsoft Excel."""
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "№ Анкеты", "Дата создания", "ФИО", "Телефон", "Вакансия",
            "Опыт", "Статус", "Категории прав", "Город", "Депо собеседования",
            "Время собеседования", "Заметка кадровика", "Платформа"
        ])

        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticket_id, created_at, full_name, phone, vacancy,
                       experience, status, driver_license, city, interview_depot,
                       interview_time, admin_note, platform
                FROM candidates
                ORDER BY ticket_id DESC
                """
            )
            for r in cur.fetchall():
                writer.writerow(list(r))

        csv_content = "\ufeff" + output.getvalue()
        filename = f"candidates_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8-sig",
        }
        return web.Response(text=csv_content, headers=headers)

    async def handle_api_admins(self, request: web.Request) -> web.Response:
        """Список всех администраторов."""
        admins = []
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, role, notify_dm, added_at FROM admins ORDER BY added_at DESC")
            for r in cur.fetchall():
                admins.append({
                    "user_id": r[0],
                    "role": r[1] or "hr",
                    "notify_dm": bool(r[2]),
                    "added_at": r[3] or "",
                })
        return web.json_response(admins)

    async def handle_api_admins_add(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        role = data.get("role", "hr")
        if uid > 0:
            self.db.add_admin(uid, role=role)
            return web.json_response({"ok": True, "user_id": uid, "role": role})
        return web.json_response({"error": "Invalid user_id"}, status=400)

    async def handle_api_admins_remove(self, request: web.Request) -> web.Response:
        data = await request.json()
        uid = int(data.get("user_id", 0))
        super_id = CONFIG.get("SUPER_ADMIN_ID", 0)
        if uid > 0 and uid != super_id:
            self.db.remove_admin(uid)
            return web.json_response({"ok": True, "user_id": uid})
        return web.json_response({"error": "Cannot remove superadmin or invalid ID"}, status=400)

    async def handle_api_admins_mute_all(self, request: web.Request) -> web.Response:
        cnt = self.db.disable_all_hr_notifications()
        return web.json_response({"ok": True, "muted_count": cnt})

    async def handle_api_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "time": datetime.now().isoformat(),
            "version": CONFIG.get("BOT_VERSION", "1.6.0"),
        })


async def start_web_panel_task(
    db: ResumeDB,
    bot: Optional[Bot] = None,
    host: str = "0.0.0.0",
    port: int = 8080
) -> None:
    """Запуск фонового веб-сервера кадровой панели и личного кабинета."""
    panel = WebPanel(db, bot=bot)
    runner = web.AppRunner(panel.app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🌐 Веб-панель управления МУП «Ульяновскэлектротранс» запущена на http://%s:%s", host, port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
