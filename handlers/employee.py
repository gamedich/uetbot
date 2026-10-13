# -*- coding: utf-8 -*-
"""
Модуль Личного кабинета сотрудника МУП «Ульяновскэлектротранс»:
- Авторизация по номеру телефона
- Реферальная программа «Приведи друга»
- Просмотр статусов приглашенных соискателей в реальном времени
- Заказ справок в отделе кадров / бухгалтерии
"""
import html

from aiogram import Router, F, types
from common import db, safe_answer, safe_send
from keyboards import (
    make_staff_main_kb,
    make_docs_kb,
    make_back_to_staff_menu_kb,
    make_auth_reply_kb,
)

employee_router = Router(name="employee")


@employee_router.callback_query(F.data == "emp_auth_menu")
async def cb_emp_auth_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    emp = db.get_employee_by_tg(user_id)

    if emp:
        text = (
            f"👤 <b>Личный кабинет сотрудника</b>\n\n"
            f"Здравствуйте, <b>{html.escape(emp['full_name'])}</b>!\n"
            f"🆔 <b>Табельный номер:</b> <code>{html.escape(str(emp['tab_number']))}</code>\n"
            f"🏢 <b>Подразделение:</b> {html.escape(emp['depot'])} | {html.escape(emp['position'])}"
        )
        return await safe_answer(
            callback, text,
            reply_markup=make_staff_main_kb(emp["tab_number"]),
            parse_mode="HTML",
        )

    text = (
        "👔 <b>АВТОРИЗАЦИЯ СОТРУДНИКА ДЕПО</b>\n\n"
        "Если вы уже работаете в МУП «Ульяновскэлектротранс», нажмите кнопку "
        "<b>«📱 Подтвердить номер телефона»</b> ниже, чтобы система нашла вас в кадровом реестре.\n\n"
        "<i>Если вас еще нет в реестре, обратитесь в отдел кадров вашего депо.</i>"
    )
    await safe_answer(callback, text, parse_mode="HTML")
    await safe_send(
        callback.bot, user_id,
        "Подтвердите номер телефона для входа:",
        reply_markup=make_auth_reply_kb(),
    )


@employee_router.message(F.contact)
async def handle_emp_contact(message: types.Message):
    phone = message.contact.phone_number
    user_id = message.from_user.id
    emp = db.get_employee_by_phone(phone)

    if emp:
        db.link_employee_tg(emp["tab_number"], user_id)
        text = (
            f"🎉 <b>Авторизация успешна, {html.escape(emp['full_name'])}!</b>\n\n"
            f"🆔 <b>Табельный номер:</b> <code>{html.escape(str(emp['tab_number']))}</code>\n"
            f"🏢 <b>Подразделение:</b> {html.escape(emp['depot'])}\n"
            f"💼 <b>Должность:</b> {html.escape(emp['position'])}"
        )
        await safe_answer(message, text, reply_markup=types.ReplyKeyboardRemove(), parse_mode="HTML")
        await safe_answer(
            message, "Ваш личный кабинет активирован:",
            reply_markup=make_staff_main_kb(emp["tab_number"]),
            parse_mode="HTML",
        )
    else:
        text = (
            f"❌ Номер <code>{html.escape(phone)}</code> не найден в реестре сотрудников.\n\n"
            "Пожалуйста, обратитесь в отдел кадров депо для внесения в базу предприятия."
        )
        await safe_answer(message, text, reply_markup=types.ReplyKeyboardRemove(), parse_mode="HTML")


@employee_router.callback_query(F.data == "staff_main_menu")
async def cb_staff_main_menu(callback: types.CallbackQuery):
    emp = db.get_employee_by_tg(callback.from_user.id)
    if not emp:
        return await safe_answer(callback, "Требуется авторизация сотрудника.", alert=True)
    text = (
        f"👤 Личный кабинет: <b>{html.escape(emp['full_name'])}</b> "
        f"(таб. <code>{html.escape(str(emp['tab_number']))}</code>)"
    )
    await safe_answer(
        callback, text,
        reply_markup=make_staff_main_kb(emp["tab_number"]),
        parse_mode="HTML",
    )


# === РЕФЕРАЛЬНАЯ ПРОГРАММА ===

@employee_router.callback_query(F.data == "ref_menu")
async def cb_ref_menu(callback: types.CallbackQuery):
    emp = db.get_employee_by_tg(callback.from_user.id)
    if not emp:
        return await safe_answer(callback, "Требуется авторизация.", alert=True)

    bot_info = await callback.bot.get_me()
    tab = html.escape(str(emp["tab_number"]))
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{tab}"

    text = (
        "🤝 <b>ПРОГРАММА «ПРИВЕДИ ДРУГА НА ПРЕДПРИЯТИЕ»</b>\n\n"
        "Приглашайте знакомых работать водителями трамваев, троллейбусов и специалистами депо!\n\n"
        "🔗 <b>Ваша персональная ссылка для друзей:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "📌 <i>Отправьте эту ссылку другу. Когда он заполнит анкету в этом боте, "
        "кадровая служба сразу увидит рекомендацию с вашим табельным номером.</i>"
    )
    await safe_answer(callback, text, reply_markup=make_back_to_staff_menu_kb(), parse_mode="HTML")


@employee_router.callback_query(F.data == "ref_my_list")
async def cb_ref_my_list(callback: types.CallbackQuery):
    emp = db.get_employee_by_tg(callback.from_user.id)
    if not emp:
        return await safe_answer(callback, "Требуется авторизация.", alert=True)

    referrals = db.get_employee_referrals(emp["tab_number"])
    if not referrals:
        text = (
            "👥 <b>ВАШИ ПРИГЛАШЕННЫЕ СОИСКАТЕЛИ</b>\n\n"
            "Пока никто не заполнил анкету по вашей ссылке.\n\n"
            "Отправьте друзьям ссылку из раздела «Пригласить друга», и их заявки появятся здесь!"
        )
        return await safe_answer(
            callback, text, reply_markup=make_back_to_staff_menu_kb(), parse_mode="HTML"
        )

    lines = [f"👥 <b>ВАШИ ПРИГЛАШЕННЫЕ (всего: {len(referrals)}):</b>\n"]
    for idx, r in enumerate(referrals, 1):
        status = html.escape(r.get("status", "Новая"))
        name = html.escape(r["full_name"])
        vac = html.escape(r["vacancy"])
        lines.append(f"{idx}. <b>{name}</b> ({vac})\n   Статус: <i>{status}</i>\n")

    await safe_answer(
        callback, "\n".join(lines), reply_markup=make_back_to_staff_menu_kb(), parse_mode="HTML"
    )


# === ЗАКАЗ СПРАВОК ===

@employee_router.callback_query(F.data == "docs_menu")
async def cb_docs_menu(callback: types.CallbackQuery):
    text = (
        "📄 <b>ЗАКАЗ СПРАВОК В ОТДЕЛЕ КАДРОВ И БУХГАЛТЕРИИ</b>\n\n"
        "Выберите документ, который вам требуется подготовить:"
    )
    await safe_answer(callback, text, reply_markup=make_docs_kb(), parse_mode="HTML")


@employee_router.callback_query(F.data.startswith("doc_req_"))
async def cb_process_doc(callback: types.CallbackQuery):
    emp = db.get_employee_by_tg(callback.from_user.id)
    if not emp:
        return await safe_answer(callback, "Требуется авторизация.", alert=True)

    types_map = {
        "doc_req_2ndfl": "Справка о доходах (2-НДФЛ)",
        "doc_req_work":  "Справка с места работы",
        "doc_req_tk":    "Копия трудовой книжки",
    }
    doc_title = types_map.get(callback.data, "Справка")
    req_id = db.add_doc_request(emp["tab_number"], emp["full_name"], doc_title)

    text = (
        f"✅ <b>Заявка #{req_id} принята!</b>\n\n"
        f"📄 <b>Документ:</b> {html.escape(doc_title)}\n"
        f"👤 <b>Заявитель:</b> {html.escape(emp['full_name'])} "
        f"(таб. <code>{html.escape(str(emp['tab_number']))}</code>)\n\n"
        "Заявка передана в отдел кадров депо."
    )
    await safe_answer(callback, text, reply_markup=make_back_to_staff_menu_kb(), parse_mode="HTML")


# === КОНТАКТЫ ДЕПО ===

@employee_router.callback_query(F.data == "emp_contacts_menu")
async def cb_emp_contacts_menu(callback: types.CallbackQuery):
    text = (
        "📞 <b>КОНТАКТЫ ПОДРАЗДЕЛЕНИЙ МУП «УЭТ»</b>\n\n"
        "🚊 <b>Засвияжское трамвайное депо №1:</b>\n"
        "• Диспетчер: 8 (8422) 58-45-XX\n"
        "• Отдел кадров: 8 (8422) 58-46-XX\n\n"
        "🚊 <b>Северное трамвайное депо №2:</b>\n"
        "• Диспетчер: 8 (8422) 46-21-XX\n\n"
        "🚎 <b>Заволжское троллейбусное депо:</b>\n"
        "• Диспетчер: 8 (8422) 52-04-XX\n\n"
        "🚨 <b>Центральная диспетчерская служба:</b>\n"
        "• Круглосуточно: 8 (8422) 42-08-XX"
    )
    await safe_answer(callback, text, reply_markup=make_back_to_staff_menu_kb(), parse_mode="HTML")