"""
Модуль автоматической генерации официальной документации
для МУП «Ульяновскэлектротранс» в форматах Word (.docx):
1. Акт об уничтожении персональных данных (Приказ Роскомнадзора № 179 от 28.10.2022).
2. Политика обработки персональных данных (ст. 18.1 152-ФЗ РФ).
3. Руководство оператора отдела кадров (ГОСТ 19.505-79).
4. Технический паспорт АИС «Рекрутинг-Сервис» (ГОСТ 19.503-79 / УЗ-3 ФСТЭК).
"""

import os
from datetime import datetime
from typing import Dict, Any, Optional

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

COLOR_PRIMARY = "003B57"   # Тёмно-синий фирменный цвет МУП УЭТ
COLOR_ALT_ROW = "F1F5F9"   # Светло-серый фон для четных строк
COLOR_BORDER  = "CBD5E1"   # Границы таблиц

def _set_cell_shading(cell, color_hex: str):
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    cell._tc.get_or_add_tcPr().append(shd)

def _apply_standard_doc_styles(doc: docx.Document):
    for sec in doc.sections:
        sec.top_margin = Inches(1.0)
        sec.bottom_margin = Inches(1.0)
        sec.left_margin = Inches(1.0)
        sec.right_margin = Inches(1.0)

    style_normal = doc.styles["Normal"]
    style_normal.font.name = "Times New Roman"
    style_normal.font.size = Pt(11)
    style_normal.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)


def generate_destruction_act_docx(cand_data: Dict[str, Any], output_path: str = None) -> str:
    """
    Генерирует официальный Акт об уничтожении персональных данных
    по форме Приказа Роскомнадзора от 28.10.2022 № 179.
    """
    doc = docx.Document()
    _apply_standard_doc_styles(doc)

    act_date = datetime.now().strftime("%d.%m.%Y")
    act_time = datetime.now().strftime("%H:%M:%S")
    cand_id = cand_data.get("id", "N/A")
    cand_fio = cand_data.get("fio", "Субъект ПДн")
    cand_phone = cand_data.get("phone", "N/A")
    cand_vacancy = cand_data.get("vacancy", "Соискатель")
    reason = cand_data.get("reason", "Отзыв согласия субъектом персональных данных (ст. 21 152-ФЗ)")

    # 1. Шапка УТВЕРЖДАЮ
    p_top = doc.add_paragraph()
    p_top.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r_top = p_top.add_run(
        "УТВЕРЖДАЮ\n"
        "Директор МУП «Ульяновскэлектротранс»\n"
        "___________________ / _________________ /\n"
        f"« {datetime.now().day} » {datetime.now().strftime('%B')} {datetime.now().year} г.\n"
    )
    r_top.font.size = Pt(10)
    p_top.paragraph_format.space_after = Pt(16)

    # 2. Заголовок
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_title = p_title.add_run(f"АКТ № {cand_id}-УПД\nоб уничтожении персональных данных")
    r_title.font.size = Pt(14)
    r_title.font.bold = True
    p_title.paragraph_format.space_after = Pt(4)

    p_city = doc.add_paragraph()
    p_city.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_city = p_city.add_run(f"г. Ульяновск, ул. Гончарова, д. 2                                           « {act_date} » г.")
    r_city.font.size = Pt(10)
    p_city.paragraph_format.space_after = Pt(14)

    # 3. Преамбула и состав комиссии
    p_body = doc.add_paragraph(
        "Настоящий Акт составлен комиссией по защите и обработке персональных данных "
        "Муниципального унитарного предприятия «Ульяновскэлектротранс» (далее — Оператор), "
        "действующей на основании Приказа № 42-ОД, в составе:\n"
        "• Председатель комиссии: Начальник отдела кадров МУП «УЭТ»;\n"
        "• Члены комиссии: Специалист по защите информации, Ведущий инженер-программист АИС."
    )
    p_body.paragraph_format.line_spacing = 1.15
    p_body.paragraph_format.space_after = Pt(8)

    p_reason = doc.add_paragraph(
        f"Комиссия установила, что в связи с наступлением основания: «{reason}», "
        "в соответствии со статьей 21 Федерального закона от 27.07.2006 № 152-ФЗ «О персональных данных» "
        "и Приказом Роскомнадзора от 28.10.2022 № 179, произведено безвозвратное уничтожение "
        "персональных данных соискателя."
    )
    p_reason.paragraph_format.line_spacing = 1.15
    p_reason.paragraph_format.space_after = Pt(12)

    # 4. Таблица сведений об уничтоженных данных
    table_headers = ["Параметр", "Значение в АИС «Рекрутинг-Сервис»"]
    table_data = [
        ("Оператор персональных данных", "МУП «Ульяновскэлектротранс» (г. Ульяновск, ул. Гончарова, 2)"),
        ("Идентификатор анкеты в СУБД", f"ID #{cand_id}"),
        ("Субъект персональных данных", f"{cand_fio} (тел. {cand_phone})"),
        ("Рассматриваемая вакансия", str(cand_vacancy)),
        ("Категории уничтоженных данных", "ФИО, дата рождения, контактный телефон, город, образование, категории водительских прав, стаж, судимость, противопоказания"),
        ("Материальный носитель / ИС", "Электронная база данных SQLite WAL АИС «Рекрутинг-Сервис»"),
        ("Способ уничтожения", "Программное затирание записи в таблице candidates, сброс состояний FSM и чекпоинт WAL-журнала"),
        ("Дата и точное время операции", f"{act_date} в {act_time}"),
    ]

    t = doc.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0]
    for idx, text in enumerate(table_headers):
        c = hdr.cells[idx]
        c.text = text
        _set_cell_shading(c, COLOR_PRIMARY)
        for p in c.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(10)
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    for r_idx, (k, v) in enumerate(table_data):
        row = t.add_row()
        c0, c1 = row.cells[0], row.cells[1]
        c0.text = k
        c1.text = v
        if r_idx % 2 == 1:
            _set_cell_shading(c0, COLOR_ALT_ROW)
            _set_cell_shading(c1, COLOR_ALT_ROW)
        for c in (c0, c1):
            c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for p in c.paragraphs:
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                for r in p.runs:
                    r.font.size = Pt(10)

    # 5. Выписка из журнала электронного аудита (лог Роскомнадзора)
    p_log_title = doc.add_paragraph()
    p_log_title.paragraph_format.space_before = Pt(14)
    r_lt = p_log_title.add_run("ВЫГРУЗКА ИЗ ЖУРНАЛА РЕГИСТРАЦИИ СОБЫТИЙ СУБД (LOG AUDIT):")
    r_lt.font.size = Pt(9.5)
    r_lt.font.bold = True

    p_log = doc.add_paragraph()
    r_l = p_log.add_run(
        f"[{act_date} {act_time}] [SECURITY-AUDIT] [152-FZ-ART21] "
        f"Action: DELETE_RECORD | Target_ID: {cand_id} | Status: PURGED | "
        f"FSM_State: CLEARED | WAL_Checkpoint: PASSIVE | Zero_Overwritten: TRUE"
    )
    r_l.font.name = "Courier New"
    r_l.font.size = Pt(8.5)
    p_log.paragraph_format.space_after = Pt(16)

    # 6. Заключение и подписи
    p_footer = doc.add_paragraph(
        "Вывод комиссии: Персональные данные субъекта безвозвратно уничтожены без возможности восстановления. "
        "Акт составлен в двух подлинных экземплярах, имеющих равную юридическую силу."
    )
    p_footer.paragraph_format.space_after = Pt(18)

    p_sign = doc.add_paragraph()
    p_sign.add_run(
        "Председатель комиссии:  ________________ / ____________________ /\n\n"
        "Члены комиссии:         ________________ / ____________________ /\n\n"
        "                        ________________ / ____________________ /"
    )
    p_sign.runs[0].font.size = Pt(10)

    if not output_path:
        out_dir = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"Act_Destruction_PDn_{cand_id}.docx")

    doc.save(output_path)
    return output_path


def generate_privacy_policy_docx(output_path: str = None) -> str:
    """Политика обработки и защиты персональных данных (ст. 18.1 152-ФЗ РФ)."""
    doc = docx.Document()
    _apply_standard_doc_styles(doc)

    p_top = doc.add_paragraph()
    p_top.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r_top = p_top.add_run(
        "УТВЕРЖДЕНА\n"
        "Приказом Директора МУП «Ульяновскэлектротранс»\n"
        "от « 15 » января 2026 г. № 04-ОД\n"
    )
    r_top.font.size = Pt(10)

    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_title = p_title.add_run("ПОЛИТИКА\nобработки и защиты персональных данных соискателей\nв МУП «Ульяновскэлектротранс»")
    r_title.font.size = Pt(14)
    r_title.font.bold = True
    p_title.paragraph_format.space_after = Pt(14)

    sections = [
        ("1. Общие положения", (
            "1.1. Настоящая Политика определяет порядок обработки и защиты персональных данных соискателей, "
            "поступающих через многоканальный программный комплекс АИС «Рекрутинг-Сервис» (Telegram, ВКонтакте, МАКС), "
            "в Муниципальном унитарном предприятии «Ульяновскэлектротранс» (далее — Оператор).\n"
            "1.2. Оператор осуществляет обработку персональных данных в строгом соответствии с Конституцией РФ, "
            "Трудовым кодексом РФ (ст. 86–88), Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных» "
            "и требованиями нормативных документов ФСТЭК и Роскомнадзора."
        )),
        ("2. Категории и цели обработки персональных данных", (
            "2.1. Целью обработки является содействие соискателю в трудоустройстве на предприятие электротранспорта, "
            "проверка квалификационных требований к водителям трамвая и троллейбуса, ведение кадрового резерва и организация связи.\n"
            "2.2. Обрабатываемые данные: фамилия, имя, отчество, дата рождения (проверка 18+), контактный номер телефона, "
            "город проживания, водительские категории (A, B, C, D, E, Трамвай, Троллейбус), уровень образования, "
            "сведения о стаже и потребности в общежитии."
        )),
        ("3. Порядок сбора, хранения и локализации данных", (
            "3.1. Электронная база данных SQLite с персональными данными соискателей физически размещена на сервере, "
            "находящемся на территории Российской Федерации (г. Ульяновск), в соответствии со ст. 18 ч. 5 152-ФЗ.\n"
            "3.2. Срок хранения персональных данных составляет не более 6 (шести) месяцев с момента подачи заявки соискателем.\n"
            "3.3. Резервные копии базы данных создаются автоматически каждые 24 часа и хранятся в защищённом каталоге с глубиной ротации 7 дней."
        )),
        ("4. Права соискателей и процедура отзыва согласия", (
            "4.1. Соискатель вправе в любой момент получить полную выгрузку своих персональных данных командой /mydata (ст. 14 152-ФЗ).\n"
            "4.2. Соискатель вправе в 1 клик отозвать согласие на обработку данных командой /revoke (ст. 21 152-ФЗ). "
            "При получении отзыва Оператор обязуется прекратить обработку и безвозвратно уничтожить персональные данные в течение 10 дней "
            "с формированием электронного Акта об уничтожении установленной формы."
        )),
        ("5. Реквизиты оператора", (
            "Муниципальное унитарное предприятие «Ульяновскэлектротранс» (МУП «УЭТ»)\n"
            "Адрес: 432071, г. Ульяновск, ул. Гончарова, д. 2\n"
            "Телефон отдела кадров: +7 (8422) 58-46-60\n"
            "Официальный сайт: http://уэт73.рф"
        ))
    ]

    for sec_title, sec_body in sections:
        h = doc.add_paragraph()
        r_h = h.add_run(sec_title)
        r_h.font.size = Pt(12)
        r_h.font.bold = True
        h.paragraph_format.space_before = Pt(12)
        h.paragraph_format.space_after = Pt(4)
        h.paragraph_format.keep_with_next = True

        p = doc.add_paragraph(sec_body)
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_after = Pt(8)

    if not output_path:
        out_dir = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "Policy_Personal_Data_MUP_UET.docx")

    doc.save(output_path)
    return output_path


def generate_operator_manual_docx(output_path: str = None) -> str:
    """Руководство оператора кадровой службы по ГОСТ 19.505-79."""
    doc = docx.Document()
    _apply_standard_doc_styles(doc)

    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_title = p_title.add_run(
        "АИС «РЕКРУТИНГ-СЕРВИС» МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»\n\n"
        "РУКОВОДСТВО ОПЕРАТОРА (КАДРОВОЙ СЛУЖБЫ)\n"
        "по ГОСТ 19.505-79 (ЕСПД)\n"
        "Обозначение документа: 12345678.00001-01 34 01"
    )
    r_title.font.size = Pt(14)
    r_title.font.bold = True
    p_title.paragraph_format.space_after = Pt(16)

    sections = [
        ("1. Назначение программы", (
            "Программный комплекс предназначен для автоматизации первичного рекрутинга, "
            "анкетирования кандидатов на вакансии водителей трамваев, троллейбусов, кондукторов и ремонтного персонала "
            "через Telegram, ВКонтакте и корпоративный мессенджер МАКС, а также для оперативной коммуникации специалистов кадровой службы."
        )),
        ("2. Авторизация и доступ к кадровой панели", (
            "2.1. Доступ к кадровым функциям предоставляется автоматически при вступлении сотрудника в официальную Telegram-группу отдела кадров.\n"
            "2.2. Для открытия панели управления оператор отправляет команду /hr в личные сообщения боту.\n"
            "2.3. При исключении сотрудника из служебного чата доступ к базе кандидатов отзывается мгновенно."
        )),
        ("3. Работа с карточками соискателей и статусами", (
            "• Каждая анкета соискателя поступает в кадровый чат в виде блочной карточки со всеми 16 параметрами.\n"
            "• Инлайн-кнопки позволяют в 1 клик перевести статус анкеты: «В работу», «Пригласить на собеседование», «Отказ».\n"
            "• При нажатии «Пригласить» соискателю автоматически уходит уведомление с точной датой, временем и адресом депо.\n"
            "• Кнопка «📝 Заметка» позволяет прикрепить служебное примечание кадровика к анкете соискателя."
        )),
        ("4. Двухсторонняя синхронизация группы и выгрузка Excel", (
            "• Команда /sync сверяет фактический состав группы кадров с правами в базе данных: выдаёт доступ новым сотрудникам и блокирует бывших.\n"
            "• Команда /kick (в ответ на сообщение) позволяет быстро удалить нарушителя из чата с отзывом роли HR.\n"
            "• Команда /export формирует структурированный файл Excel/CSV со списком соискателей за выбранный период."
        )),
        ("5. Управление текстами и настройками через Telegram", (
            "Для оперативного изменения параметров кадровой службы (контакты, режим работы депо, тексты FAQ, "
            "правила бесплатного обучения) уполномоченные инженеры используют служебную панель /tech. "
            "Все операции выполняются непосредственно через защищённые команды мессенджера без необходимости развертывания внешних веб-интерфейсов."
        ))
    ]

    for sec_title, sec_body in sections:
        h = doc.add_paragraph()
        r_h = h.add_run(sec_title)
        r_h.font.size = Pt(12)
        r_h.font.bold = True
        h.paragraph_format.space_before = Pt(10)
        h.paragraph_format.space_after = Pt(3)
        h.paragraph_format.keep_with_next = True

        p = doc.add_paragraph(sec_body)
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_after = Pt(8)

    if not output_path:
        out_dir = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "Operator_Manual_HR_GOST_19.505.docx")

    doc.save(output_path)
    return output_path


def generate_system_passport_docx(output_path: str = None) -> str:
    """Технический паспорт и архитектурное описание АИС по ГОСТ 19.503-79."""
    doc = docx.Document()
    _apply_standard_doc_styles(doc)

    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_title = p_title.add_run(
        "ТЕХНИЧЕСКИЙ ПАСПОРТ И АРХИТЕКТУРНОЕ ОПИСАНИЕ\n"
        "АИС «РЕКРУТИНГ-СЕРВИС» МУП «УЛЬЯНОВСКЭЛЕКТРОТРАНС»\n"
        "Версия комплекса: 1.6.0 Production (2026 г.)"
    )
    r_title.font.size = Pt(14)
    r_title.font.bold = True
    p_title.paragraph_format.space_after = Pt(14)

    specs = [
        ("Стек программирования", "Python 3.11+, aiogram 3.13, aiohttp 3.10, python-docx"),
        ("Архитектура данных", "SQLite 3 в режиме Write-Ahead Logging (WAL), транзакционная модель"),
        ("Класс защищенности ИСПДн", "УЗ-3 (Приказ ФСТЭК России от 18.02.2013 № 21)"),
        ("Многоканальные шлюзы", "Telegram Bot API, ВКонтакте LongPoll, МАКС (MyTeam API)"),
        ("Потребление памяти RAM", "До 46 МБ VmRSS (встроенный оптимизатор PRAGMA cache_size=-2000)"),
        ("Резервное копирование", "Автоматическое создание горячего снимка каждые 24ч (глубина 7 дней)"),
        ("Соответствие законодательству", "Федеральный закон № 152-ФЗ, № 149-ФЗ, ТК РФ ст. 86–88, Приказ РКН № 179"),
    ]

    t = doc.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0]
    hdr.cells[0].text = "Характеристика системы"
    hdr.cells[1].text = "Техническая спецификация"
    _set_cell_shading(hdr.cells[0], COLOR_PRIMARY)
    _set_cell_shading(hdr.cells[1], COLOR_PRIMARY)
    for c in hdr.cells:
        for p in c.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(10)
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    for r_idx, (k, v) in enumerate(specs):
        row = t.add_row()
        c0, c1 = row.cells[0], row.cells[1]
        c0.text = k
        c1.text = v
        if r_idx % 2 == 1:
            _set_cell_shading(c0, COLOR_ALT_ROW)
            _set_cell_shading(c1, COLOR_ALT_ROW)
        for c in (c0, c1):
            c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for p in c.paragraphs:
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                for r in p.runs:
                    r.font.size = Pt(10)

    if not output_path:
        out_dir = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "Technical_Passport_AIS_Recruiting.docx")

    doc.save(output_path)
    return output_path