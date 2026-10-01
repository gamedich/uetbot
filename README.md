# UET Recruiter Bot

> Кросс-платформенный сервис автоматизации первичного рекрутинга и сквозного анкетирования кандидатов с поддержкой 152-ФЗ РФ.

[![CI Status](https://img.shields.io/github/actions/workflow/status/uet-recruiter/bot/ci.yml?branch=beta&style=flat-square)](https://github.com/uet-recruiter/bot/actions)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue?style=flat-square)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-aiogram%203.x-2ba1ff?style=flat-square)](https://docs.aiogram.dev/)
[![Database](https://img.shields.io/badge/storage-SQLite%20WAL%20%2F%20aiosqlite-003B57?style=flat-square)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

---

## Демонстрация интерфейса

| Сценарий соискателя | Рабочее место кадровика (HR) |
| :---: | :---: |
| ![Скриншот: 16-шаговая анкета соискателя](https://via.placeholder.com/400x250.png?text=скрин:+анкета+соискателя) | ![Скриншот: Карточка тикета и действия HR](https://via.placeholder.com/400x250.png?text=скрин:+карточка+HR) |
| *Пошаговый опросник с инлайн-валидацией* | *Маршрутизация заявок, статусы, Live-Chat* |

---

## Ключевые возможности

* **16-шаговый адаптивный опросник:** сбор ФИО, возраста (строгий фильтр 18+), категорий прав (`B`, `C`, `D`, `Tm`, `Tb`), опыта и образования.
* **Юридический контур 152-ФЗ РФ:** предварительная фиксация электронной оферты, экспорт персональных данных (`/mydata`) и удаление с актом уничтожения (`/revoke`).
* **Отказоустойчивый персистентный FSM:** хранение состояний сессий в SQLite с защитой от сброса несохранённых черновиков при рестартах сервиса.
* **Анонимный Live-Chat мост:** двусторонний обмен сообщениями между кадровиком и кандидатом в реальном времени внутри тикета.
* **Умный скоринг:** автоматическое определение кандидатов без опыта для предложения программы бесплатного обучения со стипендией.
* **Кадровый экспорт:** генерация выгрузки базы откликов по команде `/export` в формат CSV/Excel с экранированием формул.
* **Инженерный пульт (`/tech`, `/git`):** горячее резервное копирование (`/backup`), восстановление (`/restore`), переключение окружений `TEST`/`PROD` и деплой в один клик.

---

## Быстрый старт

1. **Клонируйте репозиторий (ветка `beta`):**
   ```bash
   git clone -b beta [https://github.com/uet-recruiter/bot.git](https://github.com/uet-recruiter/bot.git)
   cd bot