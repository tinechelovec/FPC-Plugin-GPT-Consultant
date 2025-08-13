import os
import json
import time
import random
import logging
import g4f

from FunPayAPI.updater.events import NewMessageEvent
from locales.localizer import Localizer
from tg_bot import keyboards
from telebot.types import Message, InlineKeyboardButton

logger = logging.getLogger(f"FPC.{__name__}")

PREFIX = '[GPT Consultant]'

def log(msg):
    logger.info(f"{PREFIX} {msg}")

NAME = "GPT Info"
VERSION = "1.0"
DESCRIPTION = "GPT-консультант по товара."
UUID = "6b2c95ba-95e6-46e0-ae1c-84083993715c"
SETTINGS_PAGE = False
CREDITS = "@tinechelovec"

log("Запустил плагин GPT-консультанта (v1.0)")

# Папка и файл с настройками
PLUGIN_FOLDER = "storage/plugins/gpt_info"
DATA_FILE = os.path.join(PLUGIN_FOLDER, "data.json")
os.makedirs(PLUGIN_FOLDER, exist_ok=True)
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "command": "#info"}, f, indent=4, ensure_ascii=False)

def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения data.json: {e}")
        return {"enabled": True, "command": "#info"}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка записи data.json: {e}")


try:
    orig_edit_plugin = keyboards.edit_plugin
    def custom_edit_plugin(c, uuid, offset=0, ask_to_delete=False):
        kb = orig_edit_plugin(c, uuid, offset, ask_to_delete)
        if uuid == UUID:
            dev_btn = InlineKeyboardButton(text="👽 Разработчик", url=f"https://t.me/{CREDITS[1:]}")
            kb.keyboard[0] = [dev_btn]
        return kb
    keyboards.edit_plugin = custom_edit_plugin
except Exception:
    pass


# Основная логика генерации
def generate_gpt_response(prompt: str, max_attempts: int = 5, min_delay: float = 0.5, max_delay: float = 1.5) -> str | None:
    """
    Попробовать сгенерировать ответ через g4f до max_attempts.
    Если в ответе встретится "Login to continue using" — повторяем.
    Возвращает текст ответа или None при неудаче.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            log(f"g4f: попытка {attempt}/{max_attempts}")
            response = g4f.ChatCompletion.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}]
            )
            if not isinstance(response, str):
                response = str(response)
            response = response.strip()

            if "Login to continue using" in response or "login to continue using" in response.lower():
                log(f"g4f: обнаружена ошибка авторизации в ответе (попытка {attempt}), повторяем...")
                time.sleep(random.uniform(min_delay, max_delay))
                continue

            return response

        except Exception as e:
            logger.error(f"[g4f] Ошибка при генерации (попытка {attempt}): {e}")
            logger.debug("TRACEBACK", exc_info=True)
            time.sleep(random.uniform(min_delay, max_delay))
            continue

    return None


# Обработчик нового сообщения от FunPay
def gpt_info_handler(cardinal, e: NewMessageEvent):
    data = load_data()
    enabled = data.get("enabled", True)
    command = data.get("command", "#info")

    if not enabled:
        return

    message = e.message
    if not message.text:
        return


    if message.text.strip().lower() != command.lower():
        parts = message.text.strip().split(maxsplit=1)
        if parts and parts[0].lower() != command.lower():
            return

    log(f"Новое сообщение: {message.text} (чат {message.chat_id})")

    text = message.text.strip()
    parts = text.split(maxsplit=2)
    lot_id = None
    question = ""
    if len(parts) >= 2 and parts[1].isdigit():
        lot_id = int(parts[1])
        question = parts[2] if len(parts) > 2 else ""
    else:
        if len(parts) >= 2:
            question = parts[1] if len(parts) == 2 else parts[1] + (" " + parts[2] if len(parts) > 2 else "")
        else:
            question = ""

    if not lot_id:
        try:
            chat = cardinal.account.get_chat(message.chat_id, False)
            if chat and getattr(chat, "looking_link", None):
                lot_id = chat.looking_link.split("=")[-1]
            else:
                cardinal.send_message(message.chat_id, "Не удалось определить ID лота.")
                return
        except Exception as e:
            logger.error(f"Ошибка получения чата для определения лота: {e}")
            cardinal.send_message(message.chat_id, "Не удалось определить ID лота.")
            return

    log(f"ID товара: {lot_id}, Вопрос: {question}")

    try:
        lot_fields = cardinal.account.get_lot_fields(lot_id)
    except Exception as e:
        logger.error(f"Ошибка получения полей лота {lot_id}: {e}")
        cardinal.send_message(message.chat_id, "Не удалось получить информацию о товаре.")
        return

    title = getattr(lot_fields, "title_ru", None) or getattr(lot_fields, "title_en", None) or "[Название не указано]"
    description = getattr(lot_fields, "description_ru", None) or getattr(lot_fields, "description_en", None) or "[Описание не указано]"
    price = getattr(lot_fields, "price", "—")

    prompt = (
        f"Привет! Ты - ИИ Ассистент в нашем интернет-магазине игровых ценностей. "
        "Давай посмотрим детали и составим отличный ответ на вопрос покупателя.\n\n"
        f"Информация о товаре:\n"
        f"Название: {title}\n"
        f"Описание: {description}\n"
        f"Цена: {price} руб.\n\n"
    )

    if question:
        prompt += f"Вопрос покупателя: {question}\nОтветь на вопрос, опираясь на характеристики товара. Не добавляй лишнего.\n\n"
    else:
        prompt += "Расскажи подробно о товаре, его преимуществах и особенностях.\n\n"

    prompt += (
        "- Ответить покупателю в доброжелательном тоне.\n"
        "- Использовать умеренное количество эмодзи.\n"
        "- Красиво структурируй текст ответа: переносы строк, короткие абзацы.\n"
        "- Длина текста — до ~670 символов.\n"
    )

    response = generate_gpt_response(prompt, max_attempts=5)
    if response is None:
        cardinal.send_message(message.chat_id, "Ошибка при генерации ответа, попробуйте позже.")
        log("Не удалось получить корректный ответ от GPT после всех попыток.")
        return

    cardinal.send_message(message.chat_id, response)
    log("Отправил ответ покупателю")


# Telegram: управление плагином
user_states = {}

def set_command_start(message: Message, cardinal):
    uid = message.chat.id
    user_states[uid] = {"step": "set_command"}
    cardinal.telegram.bot.send_message(uid, "Введите новую команду, которая будет запускать GPT (например: #info или !gpt).")

def toggle_plugin_cmd(message: Message, cardinal):
    data = load_data()
    data["enabled"] = not data.get("enabled", True)
    save_data(data)
    state = "включён" if data["enabled"] else "выключен"
    cardinal.telegram.bot.send_message(message.chat.id, f"Плагин теперь {state}.")

def show_config_cmd(message: Message, cardinal):
    data = load_data()
    enabled = data.get("enabled", True)
    cmd = data.get("command", "#info")
    cardinal.telegram.bot.send_message(message.chat.id, f"Настройки GPT:\nВключен: {enabled}\nКоманда: <code>{cmd}</code>", parse_mode="HTML")

def handle_fsm_step(message: Message, cardinal):
    chat_id = message.chat.id
    if chat_id not in user_states:
        return

    text = message.text.strip()

    if text.startswith("/"):
        user_states.pop(chat_id)
        cardinal.telegram.bot.send_message(chat_id, "Операция отменена.")
        return

    state = user_states[chat_id]

    if state["step"] == "set_command":
        new_cmd = text.strip()
        if not new_cmd:
            cardinal.telegram.bot.send_message(chat_id, "Пустая команда — отменено.")
            user_states.pop(chat_id)
            return
        if new_cmd.startswith("/"):
            cardinal.telegram.bot.send_message(chat_id, "Команда не должна начинаться с '/'. Введите, например, #info или !gpt.")
            user_states.pop(chat_id)
            return

        data = load_data()
        data["command"] = new_cmd
        save_data(data)
        cardinal.telegram.bot.send_message(chat_id, f"Команда для запуска GPT обновлена: <code>{new_cmd}</code>", parse_mode="HTML")
        user_states.pop(chat_id)
        return

# Инициализация
def init_cardinal(cardinal):
    tg = cardinal.telegram
    tg.msg_handler(lambda m: set_command_start(m, cardinal), commands=["setgptcmd"])
    tg.msg_handler(lambda m: toggle_plugin_cmd(m, cardinal), commands=["gpttoggle"])
    tg.msg_handler(lambda m: show_config_cmd(m, cardinal), commands=["gptshow"])
    tg.msg_handler(lambda m: handle_fsm_step(m, cardinal), func=lambda m: m.chat.id in user_states)

    cardinal.add_telegram_commands(UUID, [
        ("setgptcmd", "Установить команду для GPT", True),
        ("gpttoggle", "Вкл/выкл GPT-плагин", True),
        ("gptshow", "Показать настройки GPT", True)
    ])

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = [gpt_info_handler]
BIND_TO_DELETE = []
