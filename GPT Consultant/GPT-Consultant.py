from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Dict, Any, Tuple, List
import os
import json
import time
import logging
import threading
import requests
import shutil

from telebot.types import Message, InlineKeyboardMarkup
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from telebot.apihelper import ApiTelegramException

import tg_bot.CBT as CBT

if TYPE_CHECKING:
    from cardinal import Cardinal
    from FunPayAPI.updater.events import NewMessageEvent

NAME = "GPT Consultant"
VERSION = "1.2"
DESCRIPTION = "GPT-консультант по товара."
CREDITS = "@tinechelovec"
UUID = "6b2c95ba-95e6-46e0-ae1c-84083993715c"
SETTINGS_PAGE = True
BIND_TO_DELETE = []

INSTRUCTION_URL = os.getenv("GPTC_INSTRUCTION_URL", "https://teletype.in/@tinechelovec/GPT-Consultant")
IO_BASE_URL = os.getenv("IOINTELLIGENCE_BASE_URL", "https://api.intelligence.io.solutions/api/v1/")
IO_CHAT_URL = os.getenv("IOINTELLIGENCE_CHAT_URL", IO_BASE_URL.rstrip("/") + "/chat/completions")
IO_MODELS_URL = os.getenv("IOINTELLIGENCE_MODELS_URL", IO_BASE_URL.rstrip("/") + "/models")
IO_MODEL = os.getenv("IOINTELLIGENCE_MODEL", "meta-llama/Llama-3.3-70B-Instruct")
IO_TIMEOUT = float(os.getenv("IOINTELLIGENCE_TIMEOUT", "45"))
IO_TEMPERATURE = float(os.getenv("IOINTELLIGENCE_TEMPERATURE", "0.2"))
IO_API_KEY_ENV = (
    os.getenv("IOINTELLIGENCE_API_KEY", "io-v2-eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lciI6IjEyMjFlYTM2LTlmZDgtNGQ3ZC1hMzY5LTMxMDZiMDk4ODMzOSIsImV4cCI6NDkyMjI5NzIwNH0.fDOOdcxznSdu9IkkbyWplIBIvTer7KrBF5GWHMQnnBwc-6GT4BDPVi8HzHx0KBOM1tKvCECcMXPBoGiyszc6tg")
    or os.getenv("IONET_API_KEY", "")
    or ""
).strip()

HISTORY_MAX_MESSAGES = int(os.getenv("GPTC_HISTORY_MAX_MESSAGES", "16"))
HISTORY_MAX_CHARS = int(os.getenv("GPTC_HISTORY_MAX_CHARS", "1200"))

logger = logging.getLogger(f"FPC.{__name__}")
PREFIX = f"[{NAME}]"

PLUGIN_FOLDER = os.path.join("storage", "plugins", "gpt_consultant")
DATA_FILE = os.path.join(PLUGIN_FOLDER, "settings.json")
LOG_FILE = os.path.join(PLUGIN_FOLDER, "plugin.log")
os.makedirs(PLUGIN_FOLDER, exist_ok=True)

_lock = threading.RLock()

def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def log(msg: str):
    try:
        logger.info(f"{PREFIX} {msg}")
    except Exception:
        pass
    try:
        with _lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{_ts()} {PREFIX} {msg}\n")
    except Exception:
        pass

def _read_last_log_lines(n: int = 25) -> str:
    try:
        if not os.path.exists(LOG_FILE):
            return "— логов пока нет —"
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = lines[-n:] if len(lines) > n else lines
        t = "".join(tail).strip()
        return t if t else "— логов пока нет —"
    except Exception as e:
        return f"⚠️ Не удалось прочитать логи: {e}"

DEFAULT_DATA: Dict[str, Any] = {
    "plugin_enabled": True,
    "mode": 1,
    "cooldown_sec": 2.0,
    "cmd_main": "/qa",
    "cmd_next": "/next",
    "io_api_key": "",
    "chat_state": {}
}

def _load_data() -> Dict[str, Any]:
    with _lock:
        if not os.path.exists(DATA_FILE):
            _save_data(DEFAULT_DATA.copy())

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}

        for k, v in DEFAULT_DATA.items():
            if k not in data:
                data[k] = v

        if not isinstance(data.get("chat_state"), dict):
            data["chat_state"] = {}

        if not isinstance(data.get("io_api_key"), str):
            data["io_api_key"] = ""

        if not isinstance(data.get("cmd_main"), str):
            data["cmd_main"] = "/qa"
        if not isinstance(data.get("cmd_next"), str):
            data["cmd_next"] = "/next"

        return data

def _save_data(data: Dict[str, Any]) -> None:
    with _lock:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log(f"Ошибка сохранения settings.json: {e}")

def _get_settings() -> Dict[str, Any]:
    data = _load_data()
    return {
        "plugin_enabled": bool(data.get("plugin_enabled", True)),
        "mode": int(data.get("mode", 1)),
        "cooldown_sec": float(data.get("cooldown_sec", 2.0)),
        "cmd_main": (data.get("cmd_main") or "/qa").strip(),
        "cmd_next": (data.get("cmd_next") or "/next").strip(),
        "io_api_key": (data.get("io_api_key") or "").strip(),
    }

def _set_settings(**updates) -> Dict[str, Any]:
    data = _load_data()
    data.update(updates)
    _save_data(data)
    return _get_settings()

def _get_api_key() -> str:
    st = _get_settings()
    key = (st.get("io_api_key") or "").strip()
    if key:
        return key
    return IO_API_KEY_ENV

def _mask_key(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return "—"
    if len(k) <= 10:
        return "********"
    return k[:6] + "…" + k[-4:]

def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"

def _get_chat_state(funpay_chat_id: Any) -> Dict[str, Any]:
    data = _load_data()
    key = str(funpay_chat_id)
    st = data["chat_state"].get(key) or {}
    st.setdefault("last_auto_reply", "")
    st.setdefault("last_ts", 0.0)
    st.setdefault("lot_id", "")
    if not isinstance(st.get("history"), list):
        st["history"] = []
    data["chat_state"][key] = st
    _save_data(data)
    return st

def _set_chat_state(funpay_chat_id: Any, **updates) -> Dict[str, Any]:
    data = _load_data()
    key = str(funpay_chat_id)
    st = data["chat_state"].get(key) or {}
    st.update(updates)
    if not isinstance(st.get("history"), list):
        st["history"] = []
    data["chat_state"][key] = st
    _save_data(data)
    return st

def _ensure_lot_history(funpay_chat_id: Any, lot_id: str) -> Dict[str, Any]:
    st = _get_chat_state(funpay_chat_id)
    prev = (st.get("lot_id") or "").strip()
    lot_id = (lot_id or "").strip()
    if lot_id and prev and prev != lot_id:
        st["history"] = []
    if lot_id and prev != lot_id:
        st["lot_id"] = lot_id
    _set_chat_state(funpay_chat_id, **st)
    return st

def _get_history(funpay_chat_id: Any, lot_id: str) -> List[Dict[str, str]]:
    st = _ensure_lot_history(funpay_chat_id, lot_id)
    hist = st.get("history") if isinstance(st.get("history"), list) else []
    cleaned: List[Dict[str, str]] = []
    for m in hist:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    if HISTORY_MAX_MESSAGES > 0 and len(cleaned) > HISTORY_MAX_MESSAGES:
        cleaned = cleaned[-HISTORY_MAX_MESSAGES:]
    return cleaned

def _append_history(funpay_chat_id: Any, lot_id: str, user_text: str, assistant_text: str):
    st = _ensure_lot_history(funpay_chat_id, lot_id)
    hist = st.get("history") if isinstance(st.get("history"), list) else []
    u = _clip(user_text, HISTORY_MAX_CHARS)
    a = _clip(assistant_text, HISTORY_MAX_CHARS)
    if u:
        hist.append({"role": "user", "content": u})
    if a:
        hist.append({"role": "assistant", "content": a})
    if HISTORY_MAX_MESSAGES > 0 and len(hist) > HISTORY_MAX_MESSAGES:
        hist = hist[-HISTORY_MAX_MESSAGES:]
    st["history"] = hist
    st["last_auto_reply"] = a or st.get("last_auto_reply", "")
    _set_chat_state(funpay_chat_id, **st)

def _state_on(v: bool) -> str:
    return "🟢 Включено" if v else "🔴 Выключено"

def _mode_label(m: int) -> str:
    return "1) простой" if int(m) == 1 else "2) контекстный"

def _cb(action: str, *parts: str) -> str:
    return f"{UUID}:{action}" + (":" + ":".join(parts) if parts else "")

def _cb_parse(data: str) -> Tuple[str, Tuple[str, ...]]:
    parts = (data or "").split(":")
    if len(parts) < 2:
        return "", tuple()
    return parts[1], tuple(parts[2:])

def _tg_safe_edit(bot, chat_id: Any, msg_id: int, text: str, kb: Optional[InlineKeyboardMarkup] = None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True
        )
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            return
    except Exception:
        return

def _tg_safe_answer(bot, call, text: str = ""):
    try:
        bot.answer_callback_query(call.id, text)
    except Exception:
        pass

def _fp_send(cardinal: "Cardinal", funpay_chat_id: Any, text: str):
    try:
        cardinal.send_message(funpay_chat_id, text)
    except Exception as e:
        log(f"send_message failed: {e}")

PAGE_HOME = "home"
PAGE_SETTINGS = "settings"
PAGE_API = "api"
PAGE_COMMANDS = "cmd"
PAGE_LOGS = "logs"

ACT_TOGGLE_PLUGIN = "toggle_plugin"
ACT_TOGGLE_MODE = "toggle_mode"
ACT_API_SET = "api_set"
ACT_API_DEL = "api_del"
ACT_CMD_SET_MAIN = "cmd_set_main"
ACT_CMD_SET_NEXT = "cmd_set_next"
ACT_LOGS_SEND = "logs_send"
ACT_LOGS_REFRESH = "logs_refresh"

ACT_DELETE_CONFIRM = "delete_confirm"
ACT_DELETE_YES = "delete_yes"
ACT_DELETE_NO = "delete_no"

ACT_FSM_CANCEL = "fsm_cancel"

CBT_PLUGINS_LIST_OPEN = f"{getattr(CBT, 'PLUGINS_LIST', '44')}:0"
CBT_EDIT_PLUGIN_KEY = getattr(CBT, "EDIT_PLUGIN", "42")
CBT_PLUGIN_SETTINGS_KEY = getattr(CBT, "PLUGIN_SETTINGS", "43")

_fsm: Dict[int, Dict[str, Any]] = {}

def _home_text() -> str:
    return (
        f"🧩 <b>Плагин:</b> <b>{NAME}</b>\n"
        f"📦 <b>Версия:</b> <code>{VERSION}</code>\n"
        f"👤 <b>Создатель:</b> <code>{CREDITS}</code>\n"
    )

def _home_kb() -> InlineKeyboardMarkup:
    kb = K()
    kb.row(
        B("⚙️ Настройки", callback_data=_cb("page", PAGE_SETTINGS)),
        B("📖 Инструкция", url=INSTRUCTION_URL),
    )
    kb.row(
        B("🗑 Удалить плагин", callback_data=_cb(ACT_DELETE_CONFIRM)),
    )
    kb.row(
        B("🔙 Меню плагинов", callback_data=CBT_PLUGINS_LIST_OPEN),
    )
    
    return kb

def _settings_text() -> str:
    st = _get_settings()
    key = _get_api_key()
    key_state = "✅ задан" if key else "❌ не задан"
    return (
        f"<b>⚙️ Настройки {NAME}</b>\n\n"
        f"• Плагин: <b>{_state_on(st['plugin_enabled'])}</b>\n"
        f"• Режим: <b>{_mode_label(st['mode'])}</b>\n"
        f"• Кулдаун: <code>{st['cooldown_sec']}</code> сек\n"
        f"• Команда: <code>{st['cmd_main']}</code>\n"
        f"• Команда далее: <code>{st['cmd_next']}</code>\n"
        f"• API ключ: <b>{key_state}</b> (<code>{_mask_key(key)}</code>)\n\n"
    )

def _settings_kb() -> InlineKeyboardMarkup:
    st = _get_settings()
    kb = K()
    kb.row(B(f"Плагин: {_state_on(st['plugin_enabled'])}", callback_data=_cb(ACT_TOGGLE_PLUGIN)))
    kb.row(B(f"Режим: {_mode_label(st['mode'])}", callback_data=_cb(ACT_TOGGLE_MODE)))
    kb.row(B("🧾 Команды", callback_data=_cb("page", PAGE_COMMANDS)))
    kb.row(B("🔑 API ключ", callback_data=_cb("page", PAGE_API)))
    kb.row(B("📜 Логи", callback_data=_cb("page", PAGE_LOGS)))
    kb.row(B("🏠 На главную", callback_data=_cb("page", PAGE_HOME)))
    return kb

def _api_text() -> str:
    key = _get_api_key()
    state = "✅ задан" if key else "❌ не задан"
    masked = _mask_key(key)
    return (
        "<b>🔑 API ключ IO Intelligence</b>\n\n"
        f"• Статус: <b>{state}</b> (<code>{masked}</code>)\n\n"
        "Нажми <b>Ввести ключ</b> и пришли ключ одним сообщением.\n"
    )

def _api_kb() -> InlineKeyboardMarkup:
    kb = K()
    kb.row(
        B("✏️ Ввести ключ", callback_data=_cb(ACT_API_SET)),
        B("🗑 Удалить ключ", callback_data=_cb(ACT_API_DEL)),
    )
    kb.row(B("◀️ Назад", callback_data=_cb("page", PAGE_SETTINGS)))
    return kb

def _commands_text() -> str:
    st = _get_settings()
    return (
        "<b>🧾 Команды</b>\n\n"
        f"• Основная команда: <code>{st['cmd_main']}</code>\n"
        f"• Команда “далее”: <code>{st['cmd_next']}</code>\n\n"
        "Покупатель пишет: <code>КОМАНДА вопрос</code>\n"
        "Пример: <code>/qa Какие сроки?</code>\n"
        "Если режим 2: <code>КОМАНДА_ДАЛЕЕ вопрос</code>\n"
        "Команда должна быть одним словом."
    )

def _commands_kb() -> InlineKeyboardMarkup:
    kb = K()
    kb.row(
        B("✏️ Изменить основную", callback_data=_cb(ACT_CMD_SET_MAIN)),
        B("✏️ Изменить “далее”", callback_data=_cb(ACT_CMD_SET_NEXT)),
    )
    kb.row(B("◀️ Назад", callback_data=_cb("page", PAGE_SETTINGS)))
    return kb

def _logs_text() -> str:
    tail = _read_last_log_lines(25)
    return (
        "<b>📜 Логи (последние строки)</b>\n\n"
        f"<code>{tail[-3500:]}</code>\n"
    )

def _logs_kb() -> InlineKeyboardMarkup:
    kb = K()
    kb.row(
        B("🔄 Обновить", callback_data=_cb(ACT_LOGS_REFRESH)),
        B("📤 Скачать log", callback_data=_cb(ACT_LOGS_SEND)),
    )
    kb.row(B("◀️ Назад", callback_data=_cb("page", PAGE_SETTINGS)))
    return kb

def _fsm_cancel_kb() -> InlineKeyboardMarkup:
    kb = K()
    kb.add(B("❌ Отменить ввод", callback_data=_cb(ACT_FSM_CANCEL)))
    return kb

def _open_page(bot, call, page: str):
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    if page == PAGE_HOME:
        _tg_safe_edit(bot, chat_id, msg_id, _home_text(), _home_kb())
    elif page == PAGE_SETTINGS:
        _tg_safe_edit(bot, chat_id, msg_id, _settings_text(), _settings_kb())
    elif page == PAGE_API:
        _tg_safe_edit(bot, chat_id, msg_id, _api_text(), _api_kb())
    elif page == PAGE_COMMANDS:
        _tg_safe_edit(bot, chat_id, msg_id, _commands_text(), _commands_kb())
    elif page == PAGE_LOGS:
        _tg_safe_edit(bot, chat_id, msg_id, _logs_text(), _logs_kb())
    else:
        _tg_safe_edit(bot, chat_id, msg_id, _home_text(), _home_kb())

def _delete_confirm_text() -> str:
    return (
        f"🗑 <b>Удаление плагина {NAME}</b>\n\n"
        "Будет удалено:\n"
        "• файл плагина\n"
        "• настройки и логи\n\n"
        "<b>Действие необратимо.</b>\n"
        "После удаления напиши: <code>/restart</code>"
    )

def _delete_confirm_kb() -> InlineKeyboardMarkup:
    kb = K()
    kb.row(
        B("✅ Да, удалить", callback_data=_cb(ACT_DELETE_YES)),
        B("❌ Отмена", callback_data=_cb(ACT_DELETE_NO)),
    )
    return kb

def _cleanup_pycache(plugin_file: str):
    try:
        pycache_dir = os.path.join(os.path.dirname(plugin_file), "__pycache__")
        if not os.path.isdir(pycache_dir):
            return
        base = os.path.splitext(os.path.basename(plugin_file))[0]
        for fn in os.listdir(pycache_dir):
            if fn.startswith(base) and fn.endswith(".pyc"):
                try:
                    os.remove(os.path.join(pycache_dir, fn))
                except Exception:
                    pass
    except Exception:
        pass

def _self_delete() -> Tuple[bool, List[str]]:
    errors: List[str] = []

    try:
        shutil.rmtree(PLUGIN_FOLDER, ignore_errors=True)
    except Exception as e:
        errors.append(f"data folder: {e}")

    plugin_file = os.path.abspath(__file__)
    plugins_dir = os.path.abspath("plugins")

    if not plugin_file.startswith(plugins_dir + os.sep):
        errors.append(f"предохранитель: файл не в папке plugins: {plugin_file}")
        return False, errors

    _cleanup_pycache(plugin_file)

    try:
        os.remove(plugin_file)
    except PermissionError:
        try:
            os.rename(plugin_file, plugin_file + ".deleted")
        except Exception as e:
            errors.append(f"remove/rename file: {e}")
    except Exception as e:
        errors.append(f"remove file: {e}")

    return (len(errors) == 0), errors

def _start_set_api_key(bot, call):
    chat_id = call.message.chat.id
    _fsm[chat_id] = {"step": "set_api_key"}
    _tg_safe_answer(bot, call, "Пришли ключ одним сообщением.")
    bot.send_message(
        chat_id,
        "🔑 Пришли API ключ одним сообщением.\n\nОтмена: /cancel",
        reply_markup=_fsm_cancel_kb()
    )

def _start_set_command(bot, call, which: str):
    chat_id = call.message.chat.id
    _fsm[chat_id] = {"step": "set_cmd", "which": which}
    _tg_safe_answer(bot, call, "Пришли новую команду.")
    bot.send_message(
        chat_id,
        "✏️ Введи новую команду одним словом.\nОтмена: /cancel",
        reply_markup=_fsm_cancel_kb()
    )

def _handle_fsm_message(message: Message, bot):
    chat_id = message.chat.id
    st = _fsm.get(chat_id)
    if not st:
        return
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "cancel", "отмена"):
        _fsm.pop(chat_id, None)
        bot.send_message(chat_id, "❌ Отменено.")
        return
    if st.get("step") == "set_api_key":
        if len(text) < 20:
            bot.send_message(chat_id, "⚠️ Ключ слишком короткий. (или /cancel)")
            return
        _set_settings(io_api_key=text)
        _fsm.pop(chat_id, None)
        bot.send_message(chat_id, "✅ API ключ сохранён.")
        log("API key updated from Telegram UI.")
        return
    if st.get("step") == "set_cmd":
        cmd = text.strip()
        if not cmd or " " in cmd:
            bot.send_message(chat_id, "⚠️ Команда должна быть одним словом. (или /cancel)")
            return
        which = st.get("which", "main")
        if which == "main":
            _set_settings(cmd_main=cmd)
            bot.send_message(chat_id, f"✅ Основная команда: <code>{cmd}</code>", parse_mode="HTML")
            log(f"cmd_main updated: {cmd}")
        else:
            _set_settings(cmd_next=cmd)
            bot.send_message(chat_id, f"✅ Команда далее: <code>{cmd}</code>", parse_mode="HTML")
            log(f"cmd_next updated: {cmd}")
        _fsm.pop(chat_id, None)
        return

def _io_chat(
    question: str,
    lot_info: Dict[str, str],
    history: List[Dict[str, str]],
) -> Tuple[Optional[str], Optional[str]]:
    api_key = _get_api_key()
    if not api_key:
        return None, "API key not set"

    sys_prompt = (
        "Ты помощник продавца на FunPay. Отвечай кратко и по делу. "
        "Используй только информацию из карточки товара (название, описание, цена). "
        "Если в карточке нет ответа — честно скажи, что информации нет, и попроси уточнить.\n\n"
        "КАРТОЧКА ТОВАРА:\n"
        f"Название: {lot_info.get('title','[неизвестно]')}\n"
        f"Описание: {lot_info.get('description','[неизвестно]')}\n"
        f"Цена: {lot_info.get('price','[неизвестно]')}\n"
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]

    for m in history:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": question.strip()})

    payload = {
        "model": IO_MODEL,
        "messages": messages,
        "temperature": IO_TEMPERATURE,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        r = requests.post(IO_CHAT_URL, json=payload, headers=headers, timeout=IO_TIMEOUT)
    except Exception as e:
        return None, f"request exception: {e}"

    if r.status_code >= 400:
        body = (r.text or "").strip().replace("\n", " ")
        return None, f"http {r.status_code}: {body[:400]}"

    try:
        data = r.json()
    except Exception:
        txt = (r.text or "").strip()
        return None, f"bad json response: {txt[:300]}"

    try:
        answer = (data["choices"][0]["message"]["content"] or "").strip()
        if not answer:
            return None, "empty answer"
        return answer, None
    except Exception:
        return None, f"unexpected response format: {str(data)[:300]}"

def _match_cmd_first_token(text: str, cmd: str) -> bool:
    t = (text or "").strip()
    if not t or not cmd:
        return False
    first = t.split(maxsplit=1)[0]
    return first.lower() == cmd.strip().lower()

def _extract_arg(text: str) -> str:
    t = (text or "").strip()
    if " " in t:
        return t.split(" ", 1)[1].strip()
    return ""

def _parse_cmd(text: str) -> Tuple[Optional[str], str]:
    st = _get_settings()
    t = (text or "").strip()
    if not t:
        return None, ""
    low = t.lower()
    if _match_cmd_first_token(t, st["cmd_main"]):
        return "main", _extract_arg(t)
    if _match_cmd_first_token(t, st["cmd_next"]):
        return "next", _extract_arg(t)
    if low.startswith(("/qa", "!qa", "/вопрос", "!вопрос")):
        return "main", _extract_arg(t)
    if low.startswith(("/далее", "!далее", "/next", "!next")):
        return "next", _extract_arg(t)
    return None, ""

def _cooldown_ok(funpay_chat_id: Any, cooldown_sec: float) -> bool:
    st = _get_chat_state(funpay_chat_id)
    now = time.time()
    return (now - float(st.get("last_ts", 0.0) or 0.0)) >= float(cooldown_sec)

def _get_lot_info(cardinal: "Cardinal", funpay_chat_id: Any) -> Tuple[Optional[Dict[str, str]], Optional[str], Optional[str]]:
    lot_id = None
    try:
        chat = cardinal.account.get_chat(funpay_chat_id, False)
        if chat and getattr(chat, "looking_link", None):
            lot_id = str(chat.looking_link).split("=")[-1]
    except Exception as e:
        return None, None, f"get_chat failed: {e}"

    if not lot_id:
        return None, None, "lot_id not found"

    try:
        lot_fields = cardinal.account.get_lot_fields(lot_id)
    except Exception as e:
        return None, lot_id, f"get_lot_fields({lot_id}) failed: {e}"

    title = getattr(lot_fields, "title_ru", None) or getattr(lot_fields, "title_en", None) or "[Название не указано]"
    description = getattr(lot_fields, "description_ru", None) or getattr(lot_fields, "description_en", None) or "[Описание не указано]"
    price = getattr(lot_fields, "price", "—")
    price_str = f"{price} руб." if price != "—" else "—"

    return {"title": str(title), "description": str(description), "price": str(price_str)}, str(lot_id), None

def _process_question(cardinal: "Cardinal", funpay_chat_id: Any, cmd_type: str, question: str):
    stg = _get_settings()

    if not stg["plugin_enabled"]:
        return

    question = (question or "").strip()
    if not question:
        _fp_send(cardinal, funpay_chat_id, "Напиши вопрос после команды.\nПример: /qa Какие сроки доставки?")
        return

    if not _cooldown_ok(funpay_chat_id, stg["cooldown_sec"]):
        return

    lot_info, lot_id, lot_err = _get_lot_info(cardinal, funpay_chat_id)
    if not lot_info or not lot_id:
        log(f"Lot info unavailable for chat {funpay_chat_id}: {lot_err}")
        _fp_send(cardinal, funpay_chat_id, "🤖 GPT временно недоступен. Попробуйте чуть позже.")
        _set_chat_state(funpay_chat_id, last_ts=time.time())
        return

    mode = int(stg["mode"])
    history = _get_history(funpay_chat_id, lot_id) if mode == 2 else []

    if cmd_type == "next":
        if mode != 2:
            _fp_send(cardinal, funpay_chat_id, "ℹ️ Команда далее работает только в режиме 2.")
            _set_chat_state(funpay_chat_id, last_ts=time.time())
            return
        if not history:
            _fp_send(cardinal, funpay_chat_id, "ℹ️ Нет истории. Сначала задай вопрос основной командой.")
            _set_chat_state(funpay_chat_id, last_ts=time.time())
            return

    answer, err = _io_chat(
        question=question,
        lot_info=lot_info,
        history=history,
    )

    if err:
        log(f"IO API error (chat {funpay_chat_id}): {err}")
        _fp_send(cardinal, funpay_chat_id, "🤖 GPT временно недоступен. Попробуйте чуть позже.")
        _set_chat_state(funpay_chat_id, last_ts=time.time())
        return

    _append_history(funpay_chat_id, lot_id, question, answer)
    _set_chat_state(funpay_chat_id, last_ts=time.time())
    _fp_send(cardinal, funpay_chat_id, answer)

def new_message_handler(cardinal: "Cardinal", event: "NewMessageEvent"):
    try:
        funpay_chat_id = getattr(getattr(event, "message", None), "chat_id", None) or getattr(event, "chat_id", None)
        text = (getattr(getattr(event, "message", None), "text", None) or "").strip()
        if not funpay_chat_id or not text:
            return

        cmd_type, arg = _parse_cmd(text)
        if not cmd_type:
            return

        log(f"Trigger in chat {funpay_chat_id}: cmd={cmd_type}, q={arg[:80]}")
        _process_question(cardinal, funpay_chat_id, cmd_type, arg)

    except Exception as e:
        log(f"new_message_handler failed: {e}")

def init_cardinal(cardinal: "Cardinal"):
    tg = cardinal.telegram
    bot = tg.bot

    try:
        cardinal.add_telegram_commands(UUID, [
            ("gptc", "Открыть панель GPT Consultant", True),
        ])
    except Exception:
        pass

    def _send_home(m: Message):
        return bot.send_message(
            m.chat.id,
            _home_text(),
            parse_mode="HTML",
            reply_markup=_home_kb(),
            disable_web_page_preview=True
        )

    tg.msg_handler(_send_home, commands=["gptc"])

    tg.msg_handler(
        lambda m: _handle_fsm_message(m, bot),
        func=lambda m: m.chat.id in _fsm,
        content_types=["text"]
    )

    def _cb_router(call):
        data = getattr(call, "data", "") or ""
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        if data.startswith(f"{UUID}:"):
            action, parts = _cb_parse(data)

            if action == "page":
                page = parts[0] if parts else PAGE_HOME
                _open_page(bot, call, page)
                _tg_safe_answer(bot, call)
                return

            if action == ACT_TOGGLE_PLUGIN:
                st = _get_settings()
                _set_settings(plugin_enabled=not st["plugin_enabled"])
                _open_page(bot, call, PAGE_SETTINGS)
                _tg_safe_answer(bot, call, "Готово")
                return

            if action == ACT_TOGGLE_MODE:
                st = _get_settings()
                new_mode = 2 if int(st["mode"]) == 1 else 1
                _set_settings(mode=new_mode)
                _open_page(bot, call, PAGE_SETTINGS)
                _tg_safe_answer(bot, call, f"Режим: {_mode_label(new_mode)}")
                return

            if action == ACT_API_SET:
                _start_set_api_key(bot, call)
                return

            if action == ACT_API_DEL:
                _set_settings(io_api_key="")
                _open_page(bot, call, PAGE_API)
                _tg_safe_answer(bot, call, "Удалено")
                log("API key deleted from Telegram UI.")
                return

            if action == ACT_CMD_SET_MAIN:
                _start_set_command(bot, call, "main")
                return

            if action == ACT_CMD_SET_NEXT:
                _start_set_command(bot, call, "next")
                return

            if action == ACT_LOGS_REFRESH:
                _open_page(bot, call, PAGE_LOGS)
                _tg_safe_answer(bot, call, "Обновлено")
                return

            if action == ACT_LOGS_SEND:
                _tg_safe_answer(bot, call, "Отправляю…")
                try:
                    if os.path.exists(LOG_FILE):
                        with open(LOG_FILE, "rb") as f:
                            bot.send_document(chat_id, f, caption="📜 plugin.log")
                    else:
                        bot.send_message(chat_id, "— лог-файла пока нет —")
                except Exception as e:
                    bot.send_message(chat_id, f"⚠️ Не удалось отправить лог: {e}")
                return

            if action == ACT_DELETE_CONFIRM:
                _tg_safe_edit(bot, chat_id, msg_id, _delete_confirm_text(), _delete_confirm_kb())
                _tg_safe_answer(bot, call)
                return

            if action == ACT_DELETE_NO:
                _open_page(bot, call, PAGE_HOME)
                _tg_safe_answer(bot, call, "Отменено")
                return

            if action == ACT_DELETE_YES:
                _tg_safe_answer(bot, call, "Удаляю…")
                ok, errs = _self_delete()
                if ok:
                    _tg_safe_edit(
                        bot,
                        chat_id,
                        msg_id,
                        "✅ <b>Плагин удалён.</b>\n\n🔁 Напиши: <code>/restart</code>",
                        None
                    )
                    log("Plugin deleted by admin UI. Restart required.")
                else:
                    log(f"Plugin delete errors: {errs}")
                    msg = "⚠️ <b>Удаление выполнено частично.</b>\n\n"
                    msg += "Ошибки:\n" + "\n".join([f"• {e}" for e in errs[:10]])
                    msg += "\n\n🔁 Всё равно напиши: <code>/restart</code>"
                    _tg_safe_edit(bot, chat_id, msg_id, msg, None)
                return

            if action == ACT_FSM_CANCEL:
                _fsm.pop(chat_id, None)
                _tg_safe_answer(bot, call, "Отменено")
                try:
                    bot.send_message(chat_id, "❌ Отменено.")
                except Exception:
                    pass
                return

            _open_page(bot, call, PAGE_HOME)
            _tg_safe_answer(bot, call)
            return

        if data == f"{UUID}:0" or data.startswith(f"{CBT_EDIT_PLUGIN_KEY}:{UUID}"):
            _tg_safe_edit(bot, chat_id, msg_id, _home_text(), _home_kb())
            _tg_safe_answer(bot, call)
            return

        if data.startswith(f"{CBT_PLUGIN_SETTINGS_KEY}:{UUID}"):
            _tg_safe_edit(bot, chat_id, msg_id, _settings_text(), _settings_kb())
            _tg_safe_answer(bot, call)
            return

    tg.cbq_handler(
        _cb_router,
        func=lambda c: (
            (getattr(c, "data", "") or "").startswith(f"{UUID}:")
            or (getattr(c, "data", "") or "") == f"{UUID}:0"
            or (getattr(c, "data", "") or "").startswith(f"{CBT_EDIT_PLUGIN_KEY}:{UUID}")
            or (getattr(c, "data", "") or "").startswith(f"{CBT_PLUGIN_SETTINGS_KEY}:{UUID}")
        )
    )

    log("Плагин запущен.")

def is_plugin_enabled_for(_: Any = None) -> bool:
    return bool(_get_settings().get("plugin_enabled", True))

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = [new_message_handler]
