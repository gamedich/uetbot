import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from aiohttp import ClientError, ClientSession
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from database import ResumeDB

logger = logging.getLogger("UET_HR_BOT")


class SQLiteFSMStorage(BaseStorage):
    """
    Персистентное хранилище состояний и данных FSM aiogram 3 в SQLite WAL.
    Гарантирует сохранение прогресса заполнения анкет при перезапуске службы.
    """

    def __init__(self, database: ResumeDB) -> None:
        self.db = database

    def _get_key_str(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_str = state.state if isinstance(state, State) else state
        k = self._get_key_str(key)
        await asyncio.to_thread(self.db.set_fsm_state, k, state_str)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        k = self._get_key_str(key)
        return await asyncio.to_thread(self.db.get_fsm_state, k)

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        k = self._get_key_str(key)
        await asyncio.to_thread(self.db.set_fsm_data, k, data)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        k = self._get_key_str(key)
        return await asyncio.to_thread(self.db.get_fsm_data, k)

    async def close(self) -> None:
        pass


class PersistentSessions(dict):
    """Персистентный кэш сессий соискателей VK / MAX с авто-сохранением в SQLite."""

    def __init__(self, database: ResumeDB) -> None:
        super().__init__()
        self.db = database
        try:
            for plat, uid, data in self.db.get_all_external_sessions():
                super().__setitem__((plat, str(uid)), data)
        except Exception:
            pass

    def __setitem__(self, key: Tuple[str, str], value: Dict[str, Any]) -> None:
        super().__setitem__(key, value)
        try:
            plat, uid = key
            self.db.set_external_session(plat, str(uid), value)
        except Exception:
            pass

    def __delitem__(self, key: Tuple[str, str]) -> None:
        if key in self:
            super().__delitem__(key)
        try:
            plat, uid = key
            self.db.delete_external_session(plat, str(uid))
        except Exception:
            pass

    def pop(self, key: Tuple[str, str], default: Any = None) -> Any:
        try:
            plat, uid = key
            self.db.delete_external_session(plat, str(uid))
        except Exception:
            pass
        return super().pop(key, default)


class NativeProxiedSession(AiohttpSession):
    """Кастомная сессия aiohttp с нативной поддержкой HTTP-туннеля для Telegram API."""

    def __init__(self, proxy_url: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.proxy_url = proxy_url

    async def create_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    async def make_request(
        self,
        bot: Bot,
        method: Any,
        timeout: Optional[int] = None,
    ) -> Any:
        session = await self.create_session()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        form = self.build_form_data(bot=bot, method=method)
        try:
            async with session.post(
                url,
                data=form,
                proxy=self.proxy_url,
                timeout=self.timeout if timeout is None else timeout,
            ) as resp:
                raw_result = await resp.text()
        except (ClientError, asyncio.TimeoutError) as proxy_err:
            if self.proxy_url:
                logger.warning(
                    "Сбой прокси %s (%s). Попытка прямого соединения с Telegram API...",
                    self.proxy_url,
                    proxy_err,
                )
                try:
                    async with session.post(
                        url,
                        data=form,
                        proxy=None,
                        timeout=self.timeout if timeout is None else timeout,
                    ) as resp:
                        raw_result = await resp.text()
                except Exception as direct_err:
                    raise TelegramNetworkError(
                        method=method,
                        message=f"Proxy error: {proxy_err} | Direct error: {direct_err}",
                    ) from direct_err
            else:
                raise TelegramNetworkError(
                    method=method, message=f"{type(proxy_err).__name__}: {proxy_err}"
                ) from proxy_err
        response = self.check_response(
            bot=bot,
            method=method,
            status_code=resp.status,
            content=raw_result,
        )
        return response.result
