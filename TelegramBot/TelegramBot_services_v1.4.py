
import os
import re
import json
import time
import threading
from datetime import datetime, timezone

import requests
import telepot
from telepot.loop import MessageLoop
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton

from MyMQTT import MyMQTT  # must be available in PYTHONPATH

SETTINGS_FILE = "settings.json"

# --- Utils & Validation ---
def normalize_mac(s: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", (s or "")).upper()

def is_valid_mac(s: str) -> bool:
    return len(normalize_mac(s)) == 12

def is_valid_username(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", s or ""))

# --- Settings ---
def load_settings(filename=SETTINGS_FILE):
    try:
        with open(filename, 'r') as f:
            settings_data = json.load(f)
            # Basic validation
            if "telegram" not in settings_data or "TOKEN" not in settings_data["telegram"]:
                raise ValueError("Missing 'telegram' or 'TOKEN' in settings.")
            if "catalog" not in settings_data or "url" not in settings_data["catalog"]:
                raise ValueError("Missing 'catalog' or 'url' in settings.")
            if "mqtt" not in settings_data or "brokerIP" not in settings_data["mqtt"] or "brokerPort" not in settings_data["mqtt"]:
                raise ValueError("Missing 'mqtt' config (brokerIP, brokerPort) in settings.")
            return settings_data
    except FileNotFoundError:
        print(f"[ERROR] Settings file '{filename}' not found.")
        raise
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ERROR] Invalid or incomplete settings file '{filename}': {e}")
        raise

# --- Catalog Client ---
class CatalogError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code

def catalog_request(method, url, json_data=None, timeout=6):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        r = requests.request(method, url, json=json_data, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json() if r.content else {}
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        detail = r.text
        try:
            detail_json = r.json()
            error_msg = detail_json.get('error', detail_json.get('detail', str(detail_json)))
        except Exception:
            error_msg = detail
        raise CatalogError(f"{method} {url} -> HTTP {status_code}: {error_msg}", status_code) from e
    except requests.RequestException as e:
        raise CatalogError(f"{method} {url} failed: {e}")

# --- Register with Catalog ---
def register_with_catalog(service_info, catalog_url, max_retries=5, base_delay=2.0):
    payload = {
        "serviceID": service_info["serviceID"],
        "name": service_info["serviceName"],
        "description": service_info.get("serviceDescription", ""),
        "type": service_info.get("serviceType", "microservice"),
        "version": service_info.get("version", "1.0.0"),
        "endpoints": service_info.get("endpoints", []),
        "status": "active",
        "timestamp": datetime.now()(timezone.utc).isoformat(),
    }
    for attempt in range(max_retries):
        try:
            r = requests.post(f"{catalog_url}/services/register", json=payload, timeout=5)
            if r.status_code in (200, 201):
                print("[REGISTER] Registered with Catalog")
                return True
            else:
                print(f"[REGISTER] Failed (attempt {attempt+1}/{max_retries}): {r.status_code}")
        except requests.RequestException as e:
            print(f"[REGISTER] Error (attempt {attempt+1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            time.sleep(base_delay * (2 ** attempt))
    return False

# --- Set Bot Descriptions ---
def set_bot_descriptions(token: str, enable: bool = True):
    if not enable or not token:
        return
    base = f"https://api.telegram.org/bot{token}"
    short = "❄️ SmartChill – Keep your fridge under control."
    desc = "👋 Welcome to SmartChill!\nMonitor your fridge, get smart alerts, and cut waste.\n\n• 🔐 Login or 🆕 Register\n• 📣 Real-time alerts\n• 🧰 Device management"
    try:
        requests.post(f"{base}/setMyShortDescription", data={"short_description": short}, timeout=5)
        requests.post(f"{base}/setMyDescription", data={"description": desc}, timeout=5)
        print("[DESC] Bot descriptions set")
    except requests.RequestException as e:
        print(f"[DESC] Failed to set descriptions: {e}")

# --- Main Bot Class ---
class TelegramBot:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        try:
            self.settings = load_settings(settings_file)
        except Exception as e:
            print(f"[FATAL] Failed to load settings: {e}")
            raise

        self.service_info = self.settings.get("serviceInfo")
        if not self.service_info or not self.service_info.get("serviceID") or not self.service_info.get("serviceName"):
            raise ValueError("Missing or incomplete 'serviceInfo' in settings.")

        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings.get("catalog", {}).get("url")
        if not self.catalog_url:
            raise ValueError("Missing 'catalog.url' in settings.")

        self.token = self.settings.get("telegram", {}).get("TOKEN")
        if not self.token or self.token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            raise ValueError("Telegram Bot TOKEN is not set or invalid in settings.")

        mqtt_cfg = self.settings.get("mqtt")
        if not mqtt_cfg or not mqtt_cfg.get("brokerIP") or not mqtt_cfg.get("brokerPort"):
            raise ValueError("Missing 'mqtt' config (brokerIP, brokerPort) in settings.")

        self.broker_host = mqtt_cfg["brokerIP"]
        self.broker_port = mqtt_cfg["brokerPort"]
        client_id = f"{mqtt_cfg.get('clientID_prefix', 'telegram_bot')}_{int(time.time())}"
        self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
        self.subscribed_topics = []
        self.connected_mqtt = False
        self.bot = telepot.Bot(self.token)

        # Commands, Callbacks, States
        self.commands = {
            "/start": {"handler": self.cmd_start, "help": "Start menu"},
            "/help": {"handler": self.cmd_help, "help": "Help & commands"},
            "/mydevices": {"handler": self.cmd_mydevices, "help": "List your devices"},
            "/showme": {"handler": self.cmd_showme, "help": "Show account info"},
            "/deleteme": {"handler": self.cmd_deleteme, "help": "Delete your account"},
            "/cancel": {"handler": self.cmd_cancel, "help": "Cancel current action"},
        }
        self.callbacks = {
            "cb_auth_login": {"handler": self.cb_auth_login},
            "cb_auth_register": {"handler": self.cb_auth_register},
            "cb_quit_menu": {"handler": self.cb_quit_menu},
            "cb_device_menu": {"handler": self.cb_device_menu},
            "cb_device_info": {"handler": self.cb_device_info},
            "cb_device_unassign": {"handler": self.cb_device_unassign},
            "cb_device_rename": {"handler": self.cb_device_rename},
        }
        self.status_handlers = {
            "waiting_for_mac": {"handler": self.handle_mac_input},
            "waiting_for_username": {"handler": self.handle_username_input},
            "waiting_for_device_rename": {"handler": self.handle_device_rename_input},
        }
        self.user_states = {}
        self.last_alert_time = {}
        self.known_devices_cache = set()
        self.running = True
        self.config_lock = threading.RLock()
        self.message_loop_thread = None

        print(f"[INIT] {self.service_id} initialized.")
        set_bot_descriptions(self.token, enable=bool(self.settings["telegram"].get("SET_DESCRIPTIONS_ON_START", True)))

    # --- Catalog wrappers ---
    def _cat_get(self, path):
        return catalog_request("GET", f"{self.catalog_url}{path}")

    def _cat_post(self, path, data):
        return catalog_request("POST", f"{self.catalog_url}{path}", json_data=data)

    def _cat_delete(self, path):
        return catalog_request("DELETE", f"{self.catalog_url}{path}")

    def _catalog_get(self, path):
        return self._cat_get(path)

    def _catalog_post(self, path, data):
        return self._cat_post(path, data)

    def _catalog_delete(self, path):
        return self._cat_delete(path)

    def _get_username(self, msg):
        user = msg.get("from", {})
        tg_username = user.get("first_name") or user.get("username")
        return tg_username if tg_username else f"User_{user.get('id', 'Unknown')}"

    def _is_registered(self, chat_id):
        """Check if a user is registered by chat_id (scans /users)."""
        try:
            users = self._cat_get("/users")
            for user in users:
                if str(user.get('telegram_chat_id')) == str(chat_id):
                    return user
            return None
        except CatalogError as e:
            print(f"[ERROR] Failed to check registration: {e}")
            return None

    def _is_chat_id_linked(self, chat_id):
        """Alias for clarity. Returns user dict if this chat_id is linked to some user."""
        return self._is_registered(chat_id)

    # --- User Ensure & Link Logic (kept for backward compatibility, NOT used in new flow) ---
    def ensure_user_exists_and_link(self, chat_id, username):
        """
        Ensure a user exists with userID = username.lower()
        and (attempt to) link telegram_chat_id = chat_id.
        Returns the confirmed user_id (lowercase username).
        """
        user_id = (username or "").lower()
        if not user_id or not is_valid_username(username):
            raise CatalogError("Invalid username format (use 3-32 letters, digits, _, ., -)")

        try:
            # Check if user exists
            existing_user = self._cat_get(f"/users/{user_id}")
            print(f"[USER_LINK] User '{user_id}' already exists. Info: {existing_user}")

            current_chat_id = existing_user.get('telegram_chat_id')
            if current_chat_id == str(chat_id):
                print(f"[USER_LINK] Chat_id {chat_id} already linked to user '{user_id}'.")
                return user_id

            try:
                self._cat_post(f"/users/{user_id}/link_telegram", {"chat_id": str(chat_id)})
                print(f"[USER_LINK] Successfully linked via /link_telegram endpoint.")
                return user_id
            except CatalogError as link_e:
                if link_e.status_code in (404, 409):
                    print(f"[USER_LINK] link_telegram not available or already linked: {link_e}")
                    return user_id
                else:
                    print(f"[WARN] Failed to link chat_id: {link_e}")
                    return user_id

        except CatalogError as e:
            if e.status_code == 404:
                print(f"[USER_LINK] User '{user_id}' not found. Creating user and linking chat_id...")
                self._cat_post("/users", {
                    "userID": user_id,
                    "userName": username,
                    "telegram_chat_id": str(chat_id)
                })
                print(f"[USER_LINK] Created user '{user_id}' and linked chat_id {chat_id}.")
            else:
                print(f"[USER_LINK] Error checking user '{user_id}': {e}")
                raise e

        return user_id

    # --- Device helpers ---
    def _find_device_by_mac(self, mac):
        try:
            normalized_mac = re.sub(r'[^0-9A-Fa-f]', '', mac).upper()
            if len(normalized_mac) != 12:
                print(f"[VALIDATION] Invalid MAC length: {mac}")
                return None
            devices = self._cat_get("/devices")
            for device in devices:
                device_mac_normalized = re.sub(r'[^0-9A-Fa-f]', '', device.get('mac_address', '')).upper()
                if device_mac_normalized == normalized_mac:
                    print(f"[CATALOG] Found device by MAC {mac}: {device.get('deviceID')}")
                    return device
            print(f"[CATALOG] Device with MAC {mac} not found.")
            return None
        except CatalogError as e:
            print(f"[CATALOG] Error searching devices by MAC: {e}")
            return None
        except Exception as e:
            print(f"[ERROR] Unexpected error in _find_device_by_mac: {e}")
            return None

    # --- State Management ---
    def set_status(self, chat_id, state_name, **kwargs):
        if state_name not in self.status_handlers:
            print(f"[WARN] Unknown state: {state_name}")
            return
        self.user_states[chat_id] = {"state": state_name, "data": kwargs}
        print(f"[STATE] {chat_id} -> {state_name}")

    def get_status(self, chat_id):
        return self.user_states.get(chat_id)

    def clear_status(self, chat_id):
        removed_state = self.user_states.pop(chat_id, None)
        if removed_state:
            print(f"[STATE] {chat_id} exit {removed_state['state']}")
        return removed_state

    # --- Standard Service Methods ---
    def extract_mqtt_topics(self):
        subscribe_topics, publish_topics = [], []
        for endpoint in self.service_info.get("endpoints", []):
            if endpoint.startswith("MQTT Subscribe: "):
                subscribe_topics.append(endpoint.replace("MQTT Subscribe: ", "").strip())
            elif endpoint.startswith("MQTT Publish: "):
                publish_topics.append(endpoint.replace("MQTT Publish: ", "").strip())
        return subscribe_topics, publish_topics

    def register_with_catalog(self):
        print(f"[REGISTER] Registering {self.service_id} with catalog...")
        try:
            service_payload = {
                "serviceID": self.service_info.get("serviceID"),
                "name": self.service_info.get("serviceName"),
                "description": self.service_info.get("serviceDescription"),
                "type": self.service_info.get("serviceType", "interface_bot"),
                "version": self.service_info.get("version", "1.0.0"),
                "endpoints": self.service_info.get("endpoints", []),
                "status": "active"
            }
            if not service_payload["serviceID"] or not service_payload["name"]:
                raise ValueError("Missing serviceID or name in serviceInfo settings")
            response = self._cat_post("/services/register", service_payload)
            print(f"[REGISTER] Service registration successful: {response.get('status', 'OK')}")
            return True
        except (CatalogError, ValueError) as e:
            print(f"[REGISTER] Service registration failed: {e}")
            return False
        except Exception as e:
            print(f"[REGISTER] Unexpected error during service registration: {e}")
            return False

    def setup_mqtt(self):
        try:
            if self.mqtt_client.start():
                self.connected_mqtt = True
                print("[INIT] MQTT Connected.")
                subscribe_topics, _ = self.extract_mqtt_topics()
                print(f"[MQTT] Attempting to subscribe to {len(subscribe_topics)} topics...")
                successful_subs = 0
                for topic in subscribe_topics:
                    if self.mqtt_client.mySubscribe(topic):
                        self.subscribed_topics.append(topic)
                        successful_subs += 1
                        print(f"[MQTT] Subscribed to: {topic}")
                    else:
                        print(f"[ERROR] Failed to initiate subscription for: {topic}")
                if successful_subs == 0 and len(subscribe_topics) > 0:
                    print("[WARN] Failed to initiate any MQTT subscriptions!")
                return True
            else:
                print("[ERROR] MyMQTT.start() reported connection failure.")
                self.connected_mqtt = False
                return False
        except Exception as e:
            print(f"[ERROR] Exception during MQTT setup: {e}")
            self.connected_mqtt = False
            return False

    # --- Telegram Command Handlers ---
    def cmd_start(self, chat_id, msg, *args):
        username = self._get_username(msg)
        self.bot.sendMessage(chat_id, f"👋 Welcome, {username}!")
        existing_user = self._is_registered(chat_id)
        if existing_user and existing_user.get("devicesList"):
            self.bot.sendMessage(chat_id, "You seem to be already set up. Use /mydevices or /help.")
            self.clear_status(chat_id)
            return
        self.bot.sendMessage(
            chat_id,
            "To link your SmartChill account, please enter the **MAC address** of your fridge.\n"
            "(Format: `XX:XX:XX:XX:XX:XX` or `AABBCC112233`)",
            parse_mode="Markdown"
        )
        self.set_status(chat_id, "waiting_for_mac")

    def cmd_help(self, chat_id, msg, *args):
        lines = [f"{c} – {m['help']}" for c, m in self.commands.items()]
        self.bot.sendMessage(chat_id, "Commands:\n" + "\n".join(lines))

    def cmd_mydevices(self, chat_id, msg, *args):
        user_data = self._is_registered(chat_id)
        if not user_data:
            self.bot.sendMessage(chat_id, "You need to be logged in/registered first. Use /start.")
            return
        checking_msg = self.bot.sendMessage(chat_id, "🔄 Fetching your devices...")
        try:
            user_id = user_data.get('userID')
            if not user_id:
                raise CatalogError("Could not determine your User ID.")
            user_devices_list = self._catalog_get(f"/users/{user_id}/devices")
            if not user_devices_list:
                self.bot.editMessageText(telepot.message_identifier(checking_msg), "You have no assigned devices.\nUse /start to link your first device.")
                return
            buttons = [[InlineKeyboardButton(text=d.get('user_device_name', d['deviceID']), callback_data=f"cb_device_menu {d['deviceID']}")] for d in user_devices_list]
            buttons.append([InlineKeyboardButton(text="Close Menu", callback_data='cb_quit_menu')])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            self.bot.editMessageText(telepot.message_identifier(checking_msg), "Your Devices:", reply_markup=keyboard)
        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(checking_msg), f"⚠️ Error fetching devices: {e}")

    def cmd_showme(self, chat_id, msg, *args):
        checking_msg = self.bot.sendMessage(chat_id, "🔄 Fetching your info...")
        user_data = self._is_registered(chat_id)
        if not user_data:
            self.bot.editMessageText(telepot.message_identifier(checking_msg), "You are not registered. Use /start.")
            return
        devices_count = len(user_data.get('devicesList', []))
        message = (f"👤 **User Info**\n"
                   f"**Catalog UserID:** `{user_data['userID']}`\n"
                   f"**Telegram Name:** {user_data['userName']}\n"
                   f"**Telegram ChatID:** `{chat_id}`\n"
                   f"**Registered:** {user_data.get('registration_time', 'N/A')}\n"
                   f"**Assigned Devices:** {devices_count}")
        self.bot.editMessageText(telepot.message_identifier(checking_msg), message, parse_mode="Markdown")

    def cmd_deleteme(self, chat_id, msg, *args):
        checking_msg = self.bot.sendMessage(chat_id, "🔄 Checking registration status...")
        user_data = self._is_registered(chat_id)
        if not user_data:
            self.bot.editMessageText(telepot.message_identifier(checking_msg), "You are already not registered.")
            return
        user_id_to_delete = user_data.get('userID')
        if not user_id_to_delete:
            self.bot.editMessageText(telepot.message_identifier(checking_msg), "❌ Could not determine your User ID to delete.")
            return

        confirming_msg = self.bot.sendMessage(chat_id, f"Deleting user '{user_id_to_delete}' and unassigning devices...")
        try:
            delete_response = self._catalog_delete(f"/users/{user_id_to_delete}")
            unassigned_count = delete_response.get("unassigned_count", 0)
            self.bot.editMessageText(telepot.message_identifier(confirming_msg), f"✅ User {user_data['userName']} deleted. {unassigned_count} devices were unassigned.")
        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(confirming_msg), f"❌ Deletion failed: {e}")
        except Exception as e:
            self.bot.editMessageText(telepot.message_identifier(confirming_msg), f"❌ Deletion failed with unexpected error: {e}")

    def cmd_cancel(self, chat_id, msg, *args):
        removed_state = self.clear_status(chat_id)
        if removed_state:
            self.bot.sendMessage(chat_id, "Operation cancelled.")
        else:
            self.bot.sendMessage(chat_id, "No active operation to cancel.")


    def cancel_command(self, chat_id, msg):
        self.cmd_cancel(chat_id, msg)

    # --- Telegram Callback Handlers ---
    def cb_auth_login(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        self.bot.sendMessage(chat_id, "Login flow not implemented yet.")

    def cb_auth_register(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        self.bot.sendMessage(chat_id, "Registration flow not implemented yet.")

    def cb_quit_menu(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        try:
            if 'reply_markup' in msg_query['message']:
                self.bot.editMessageReplyMarkup(telepot.message_identifier(msg_query['message']))
            self.bot.editMessageText(telepot.message_identifier(msg_query['message']), "Menu closed.")
        except telepot.exception.TelegramError as e:
            if "message is not modified" not in str(e) and "message to edit not found" not in str(e):
                print(f"[WARN] Failed to edit message on quit: {e}")

    def cb_device_menu(self, query_id, chat_id, msg_query, *args):
        device_id = args[0] if args else None
        if not device_id:
            print("[WARN] cb_device_menu missing device_id")
            self.bot.answerCallbackQuery(query_id, "Error: Missing device ID.")
            return
        self.bot.answerCallbackQuery(query_id)
        device_display = f"`{self.escape_markdown(device_id)}`"
        buttons = [
            [InlineKeyboardButton(text="ℹ️ Show Info", callback_data=f"cb_device_info {device_id}")],
            [InlineKeyboardButton(text="✏️ Rename Device", callback_data=f"cb_device_rename {device_id}")],
            [InlineKeyboardButton(text="❌ Unassign Device", callback_data=f"cb_device_unassign {device_id}")],
            [InlineKeyboardButton(text="« Back", callback_data="/mydevices")],
            [InlineKeyboardButton(text="Close Menu", callback_data="cb_quit_menu")]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        self.bot.editMessageText(telepot.message_identifier(msg_query['message']), f"Options for device {device_display}:", reply_markup=keyboard, parse_mode="Markdown")

    def escape_markdown(self, text):
        """Escape special characters for Telegram Markdown."""
        if text is None:
            return "N/A"
        # Escape Markdown special characters
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        text = str(text)
        for char in special_chars:
            text = text.replace(char, '\\' + char)
        return text

    def cb_device_info(self, query_id, chat_id, msg_query, *args):
        device_id = args[0] if args else None
        if not device_id:
            print("[WARN] cb_device_info missing device_id")
            self.bot.answerCallbackQuery(query_id, "Error: Missing device ID.")
            return
        self.bot.answerCallbackQuery(query_id)
        info_msg = self.bot.editMessageText(telepot.message_identifier(msg_query['message']), f"🔄 Fetching info for `{device_id}`...")
        try:
            device_info = self._catalog_get(f"/devices/{device_id}")

            # Build message with escaped values
            lines = []
            lines.append(f"**Device:** `{self.escape_markdown(device_info['deviceID'])}`")
            lines.append(f"**Name:** {self.escape_markdown(device_info.get('user_device_name', 'N/A'))}")
            lines.append(f"**Model:** {self.escape_markdown(device_info.get('model', 'N/A'))}")
            lines.append(f"**Firmware:** {self.escape_markdown(device_info.get('firmware_version', 'N/A'))}")

            sensors = device_info.get('sensors', [])
            if sensors:
                sensors_text = ', '.join([self.escape_markdown(s) for s in sensors])
                lines.append(f"**Sensors:** {sensors_text}")
            else:
                lines.append(f"**Sensors:** N/A")

            lines.append(f"**Status:** {self.escape_markdown(device_info.get('status', 'N/A'))}")

            if device_info.get('user_assigned'):
                assigned_user = self.escape_markdown(str(device_info.get('assigned_user', 'Unknown')))
                lines.append(f"**Assigned:** Yes, to {assigned_user}")
            else:
                lines.append(f"**Assigned:** No")

            lines.append(f"**Last Sync:** {self.escape_markdown(device_info.get('last_sync', 'N/A'))}")

            current_markup = msg_query['message'].get('reply_markup')
            self.bot.editMessageText(
                telepot.message_identifier(info_msg), 
                "\n".join(lines), 
                parse_mode="Markdown", 
                reply_markup=current_markup
            )
        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(info_msg), f"❌ Error fetching info: {e}")

    def cb_device_unassign(self, query_id, chat_id, msg_query, *args):
        device_id = args[0] if args else None
        if not device_id:
            print("[WARN] cb_device_unassign missing device_id")
            self.bot.answerCallbackQuery(query_id, "Error: Missing device ID.")
            return
        self.bot.answerCallbackQuery(query_id)
        unassigning_msg = self.bot.editMessageText(telepot.message_identifier(msg_query['message']), f"🔄 Unassigning `{self.escape_markdown(device_id)}`...")
        try:
            response = self._catalog_post(f"/devices/{device_id}/unassign", data=None)
            self.bot.editMessageText(telepot.message_identifier(unassigning_msg), f"✅ Device `{self.escape_markdown(device_id)}` unassigned successfully.", parse_mode="Markdown")
            self.bot.sendMessage(chat_id, "Use /mydevices to see your updated list.")
        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(unassigning_msg), f"❌ Unassignment failed: {e}")

    def cb_device_rename(self, query_id, chat_id, msg_query, *args):
        """Callback to initiate device rename flow"""
        device_id = args[0] if args else None
        if not device_id:
            print("[WARN] cb_device_rename missing device_id")
            self.bot.answerCallbackQuery(query_id, "Error: Missing device ID.")
            return

        self.bot.answerCallbackQuery(query_id)

        # Get current device name
        try:
            device_info = self._catalog_get(f"/devices/{device_id}")
            current_name = device_info.get('user_device_name', 'N/A')

            self.bot.editMessageText(
                telepot.message_identifier(msg_query['message']),
                f"✏️ **Rename Device**\n\n"
                f"Device: `{self.escape_markdown(device_id)}`\n"
                f"Current name: *{self.escape_markdown(current_name)}*\n\n"
                f"Please send the new name for this device.\n"
                f"(Use /cancel to abort)",
                parse_mode="Markdown"
            )

            # Set state to wait for new name
            self.set_status(chat_id, "waiting_for_device_rename", device_id=device_id, old_name=current_name)

        except CatalogError as e:
            self.bot.editMessageText(
                telepot.message_identifier(msg_query['message']),
                f"❌ Error fetching device info: {e}"
            )

    # --- Telegram State Handlers ---
    def handle_mac_input(self, chat_id, msg, state_data):
        """
        REVISED FLOW:
        1) Validate MAC + lookup device
        2) If assigned:
             - if chat matches existing user's chat -> Login OK ("Bentornato")
             - else -> error: "Device assigned to another user"
           End
        3) If free:
             - Check if THIS chat_id is already linked to a user in catalog
                 - If YES -> Assign THIS free device to THAT existing user. Success. -> End
                 - If NO -> Ask for username -> set state waiting_for_username
        """
        mac_input = (msg.get("text") or "").strip()
        if not is_valid_mac(mac_input):
            self.bot.sendMessage(
                chat_id,
                "⚠️ Invalid MAC address format. Please use `XX:XX:XX:XX:XX:XX` or `AABBCC112233`.\nType /cancel to stop.",
                parse_mode="Markdown"
            )
            return

        processing_msg = self.bot.sendMessage(chat_id, f"🔎 Checking MAC `{mac_input}`...")
        try:
            device_info = self._find_device_by_mac(mac_input)
            if not device_info:
                self.bot.editMessageText(
                    telepot.message_identifier(processing_msg),
                    f"❌ Error: MAC `{mac_input}` not found.\nPlease check it and try again using /start.",
                    parse_mode="Markdown"
                )
                self.clear_status(chat_id)
                return

            device_id = device_info['deviceID']
            is_assigned = device_info.get('user_assigned', False)
            assigned_user = device_info.get('assigned_user') # This is the userID (lowercase username)

            if is_assigned:
                # Device already assigned -> check chat
                linked_user_for_chat = self._is_chat_id_linked(chat_id) # Check if *this* chat is linked
                
                # Compare the userID the device is assigned to with the userID linked to this chat
                if linked_user_for_chat and str(assigned_user).lower() == linked_user_for_chat.lower():
                    # Login success for same user
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Welcome back! The device `{self.escape_markdown(device_id)}` is already linked to your account.\nUse /mydevices to manage it.",
                        parse_mode="Markdown"
                    )
                else:
                    # Assigned to another user, or this chat isn't linked to the assigned user
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"⛔️ This device is already assigned to user '{assigned_user}'.\nIf you believe this is an error, please contact support.",
                        parse_mode="Markdown"
                    )
                self.clear_status(chat_id)
                return

            # --- Device is FREE (`is_assigned == False`) ---
            else:
                # Check if THIS chat_id is already linked to an existing user
                existing_user_for_chat = self._is_registered(chat_id) # Reuse _is_registered which returns user dict

                if existing_user_for_chat:
                    # Chat ID already linked -> Assign THIS device to THIS existing user
                    user_id = existing_user_for_chat['userID']
                    username = existing_user_for_chat['userName']  # Get username for message

                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Existing account '{user_id}' found. Linking device `{self.escape_markdown(device_id)}`...",
                        parse_mode="Markdown"
                    )

                    # Assign Device
                    assign_response = self._catalog_post(f"/users/{user_id}/assign-device", {
                        "device_id": device_id,
                        "device_name": f"{username}'s Fridge"  # Use existing username
                    })
                    final_name = assign_response.get("device", {}).get('user_device_name', f"{username}'s Fridge")

                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Device `{self.escape_markdown(device_id)}` successfully linked to your account as '{self.escape_markdown(final_name)}'. Use \mydevice to manage your devices",
                        parse_mode="Markdown"
                    )
                    self.clear_status(chat_id)
                    return

                else:
                    # Device free AND chat_id free -> Proceed to ask for username for NEW user registration
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Valid MAC address: `{self.escape_markdown(device_id)}` found and available!\n\n"
                        "This Telegram account is not yet registered. Please send your desired **username** to complete the registration (it will serve as your unique ID).",
                        parse_mode="Markdown"
                    )
                    self.set_status(chat_id, "waiting_for_username", mac_address=mac_input, device_id=device_id)


        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(processing_msg), f"❌ Operazione fallita: {e}")
            self.clear_status(chat_id)
        except Exception as e:
            print(f"[ERROR] Unexpected error in handle_mac_input: {e}")
            self.bot.editMessageText(telepot.message_identifier(processing_msg), "❌ Errore inatteso.")
            self.clear_status(chat_id)
            import traceback; traceback.print_exc() # Log full error for debugging

    def handle_username_input(self, chat_id, msg, state_data):
        """
        NEW FLOW:
        - Valida username
        - Recupera device_id dallo stato
        - Controllo esistenza username (/users/{userID})
            * se esiste -> "Username già in uso" (resta nello stato)
            * se 404 -> Crea utente con telegram_chat_id e assegna device.
        """
        username = (msg.get("text") or "").strip()
        mac_address = state_data.get("mac_address")
        device_id = state_data.get("device_id")

        if not is_valid_username(username):
            self.bot.sendMessage(
                chat_id,
                "⚠️ Invalid username (must be 3–32 characters: letters, numbers, _, ., or -). Type /cancel to stop."
            )
            return

        if not mac_address or not device_id:
            self.bot.sendMessage(
                chat_id,
                "Internal error: missing MAC address or Device ID. Type /cancel to stop."
            )
            self.clear_status(chat_id)
            return

        user_id = username.lower()
        processing_msg = self.bot.sendMessage(
            chat_id,
            f"🔄 Checking availability for username `{user_id}` and linking the device...",
            parse_mode="Markdown"
        )

        try:
            # Check if username exists
            try:
                _ = self._catalog_get(f"/users/{user_id}")
                # Exists -> ask for another
                self.bot.editMessageText(
                    telepot.message_identifier(processing_msg),
                    "❌ Username already in use. Please choose another one."
                )
                # remain in state waiting_for_username (keep device_id)
                return
            except CatalogError as e:
                if e.status_code != 404:
                    # Any error except 404 should bubble up
                    raise

            # Create user
            self._catalog_post("/users", {
                "userID": user_id,
                "userName": username,
                "telegram_chat_id": str(chat_id)
            })

            # Assign device
            assign_response = self._catalog_post(f"/users/{user_id}/assign-device", {
                "device_id": device_id
            })
            final_name = assign_response.get("device", {}).get('user_device_name') or f"{username}'s Fridge"

            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                f"✅ Registration completed!\n"
                f"Device `{self.escape_markdown(device_id)}` successfully linked as *{self.escape_markdown(final_name)}*.\n"
                f"Use /mydevices to manage your devices.",
                parse_mode="Markdown"
            )
            self.clear_status(chat_id)

        except CatalogError as e:
            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                f"❌ Operation failed: {e}"
            )
            self.clear_status(chat_id)
        except Exception as e:
            print(f"[ERROR] Unexpected error in handle_username_input: {e}")
            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                "❌ Unexpected error occurred."
            )
            self.clear_status(chat_id)


    def handle_device_rename_input(self, chat_id, msg, state_data):
        """Handle new device name input"""
        new_name = msg.get("text", "").strip()
        device_id = state_data.get("device_id")
        old_name = state_data.get("old_name")

        if not new_name:
            self.bot.sendMessage(chat_id, "⚠️ The device name cannot be empty. Please send a valid name or type /cancel.")
            return

        if len(new_name) > 50:
            self.bot.sendMessage(chat_id, "⚠️ The name is too long (max 50 characters). Please try again or type /cancel.")
            return

        if not device_id:
            self.bot.sendMessage(chat_id, "⚠️ Internal error: missing Device ID. Type /cancel to stop.")
            self.clear_status(chat_id)
            return

        processing_msg = self.bot.sendMessage(chat_id, f"🔄 Renaming device to '{new_name}'...")

        try:
            # Get user_id from chat_id
            user_data = self._is_registered(chat_id)
            if not user_data:
                self.bot.editMessageText(
                    telepot.message_identifier(processing_msg),
                    "❌ Error: you must be logged in to rename your devices."
                )
                self.clear_status(chat_id)
                return

            user_id = user_data.get('userID')

            # Try to rename via catalog endpoint
            try:
                self._cat_post(f"/devices/{device_id}/rename", {"user_device_name": new_name})
                self.bot.editMessageText(
                    telepot.message_identifier(processing_msg),
                    f"✅ Device renamed successfully!\n\n"
                    f"Previous name: *{self.escape_markdown(old_name)}*\n"
                    f"New name: *{self.escape_markdown(new_name)}*",
                    parse_mode="Markdown"
                )
            except CatalogError as e:
                if e.status_code == 404:
                    print(f"[RENAME] /devices/{device_id}/rename not available, using unassign/reassign fallback")
                    self._catalog_post(f"/devices/{device_id}/unassign", data=None)
                    self._catalog_post(f"/users/{user_id}/assign-device", {
                        "device_id": device_id,
                        "device_name": new_name
                    })

                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Device renamed successfully!\n\n"
                        f"Previous name: *{self.escape_markdown(old_name)}*\n"
                        f"New name: *{self.escape_markdown(new_name)}*",
                        parse_mode="Markdown"
                    )
                else:
                    raise e

            self.bot.sendMessage(chat_id, "Use /mydevices to view your updated devices list.")
            self.clear_status(chat_id)

        except CatalogError as e:
            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                f"❌ Rename failed: {e}"
            )
            self.clear_status(chat_id)
        except Exception as e:
            print(f"[ERROR] Unexpected error in handle_device_rename_input: {e}")
            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                "❌ Unexpected error during rename."
            )
            self.clear_status(chat_id)


    # --- Main Message Handling Logic ---
    def handle_my_chat_member(self, msg):
        """Handles my_chat_member updates (bot blocked/unblocked, added to groups, etc.)"""
        try:
            chat = msg.get('chat', {})
            chat_id = chat.get('id', 'Unknown')
            chat_type = chat.get('type', 'unknown')
            new_member = msg.get('new_chat_member', {})
            old_member = msg.get('old_chat_member', {})
            new_status = new_member.get('status', 'unknown')
            old_status = old_member.get('status', 'unknown')

            print(f"[MY_CHAT_MEMBER] Chat {chat_id} ({chat_type}): {old_status} -> {new_status}")

            if new_status == 'kicked':
                print(f"[MY_CHAT_MEMBER] Bot was blocked/kicked by user/chat {chat_id}")
                self.clear_status(chat_id)
            elif new_status == 'member':
                print(f"[MY_CHAT_MEMBER] Bot was unblocked/added to chat {chat_id}")
            elif new_status == 'left':
                print(f"[MY_CHAT_MEMBER] Bot left chat {chat_id}")
                self.clear_status(chat_id)

        except Exception as e:
            print(f"[WARN] Error handling my_chat_member update: {e}")
            import traceback
            traceback.print_exc()

    def handle_telegram_update(self, update):
        """
        Main router for all Telegram updates.
        Handles both standard messages and special updates like my_chat_member.
        """
        try:
            if 'my_chat_member' in update:
                self.handle_my_chat_member(update['my_chat_member'])
                return

            if 'chat_member' in update:
                print(f"[INFO] Ignoring chat_member update")
                return

            msg = None
            if 'message' in update:
                msg = update['message']
                self.handle_message(msg)
            elif 'edited_message' in update:
                print(f"[INFO] Ignoring edited_message update")
            elif 'callback_query' in update:
                msg = update['callback_query']
                self.handle_callback_query(msg)
            elif 'channel_post' in update:
                print(f"[INFO] Ignoring channel_post update")
            elif 'edited_channel_post' in update:
                print(f"[INFO] Ignoring edited_channel_post update")
            elif 'inline_query' in update:
                print(f"[INFO] Ignoring inline_query update")
            elif 'chosen_inline_result' in update:
                print(f"[INFO] Ignoring chosen_inline_result update")
            else:
                print(f"[INFO] Ignoring unknown update type. Keys: {list(update.keys())}")

        except Exception as e:
            print(f"[ERROR] Error in handle_telegram_update: {e}")
            import traceback
            traceback.print_exc()

    def handle_message(self, msg):
        chat_id = None
        try:
            content_type, chat_type, chat_id = telepot.glance(msg)
            if content_type != "text":
                self.bot.sendMessage(chat_id, "Sorry, I only understand text commands.")
                return
            text = msg["text"].strip()
            if text.lower().startswith("/cancel"):
                self.cancel_command(chat_id, msg)
                return
            current_status = self.get_status(chat_id)
            if current_status:
                state_name = current_status["state"]
                handler_info = self.status_handlers.get(state_name)
                if handler_info and callable(handler_info["handler"]):
                    print(f"[STATE] Handling message via state: {state_name}")
                    handler_info["handler"](chat_id, msg, current_status["data"])
                    return
                else:
                    print(f"[WARN] Invalid handler for state: {state_name}")
                    self.clear_status(chat_id)
                    self.bot.sendMessage(chat_id, "Internal error. Operation cancelled.")
            if text.startswith("/"):
                parts = text.split()
                command = parts[0].lower()
                args = parts[1:]
                command_info = self.commands.get(command)
                if command_info and callable(command_info["handler"]):
                    print(f"[COMMAND] Executing: {command} with args: {args}")
                    try:
                        command_info["handler"](chat_id, msg, *args)
                    except Exception as e:
                        print(f"[ERROR] Exception in command '{command}': {e}")
                        self.bot.sendMessage(chat_id, f"⚠️ Error running {command}.")
                        import traceback
                        traceback.print_exc()
                else:
                    self.bot.sendMessage(chat_id, f"Unknown command: {command}. Use /help.")
            else:
                self.bot.sendMessage(chat_id, "Use commands starting with / or reply when prompted.")
        except Exception as e:
            print(f"[ERROR] Unhandled exception in handle_message: {e}")
            import traceback
            traceback.print_exc()
            if chat_id:
                try:
                    self.bot.sendMessage(chat_id, "An unexpected error occurred.")
                except:
                    pass

    def handle_callback_query(self, msg_query):
        query_id, from_id, query_data = None, None, None
        try:
            query_id, from_id, query_data = telepot.glance(msg_query, flavor='callback_query')
            chat_id = from_id
            print(f"[CALLBACK] Received query: {query_data} from {chat_id}")
            self.bot.answerCallbackQuery(query_id)
            if query_data.startswith("/"):
                command_info = self.commands.get(query_data.lower())
                if command_info and callable(command_info["handler"]):
                    print(f"[CALLBACK] Routing to command: {query_data}")
                    command_info["handler"](chat_id, msg_query.get('message', {}))
                else:
                    print(f"[WARN] Callback command not found: {query_data}")
                    self.bot.sendMessage(chat_id, f"Unknown action: {query_data}")
                return
            parts = query_data.split()
            callback_key = parts[0].lower()
            args = parts[1:]
            callback_info = self.callbacks.get(callback_key)
            if callback_info and callable(callback_info["handler"]):
                print(f"[CALLBACK] Executing: {callback_key} with args: {args}")
                try:
                    callback_info["handler"](query_id, chat_id, msg_query, *args)
                except Exception as e:
                    print(f"[ERROR] Exception in callback '{callback_key}': {e}")
                    self.bot.sendMessage(chat_id, f"⚠️ Error during action '{callback_key}'.")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"[WARN] Unknown callback action: {callback_key}")
                self.bot.sendMessage(chat_id, f"Unknown action: {callback_key}")
        except Exception as e:
            print(f"[ERROR] Unhandled exception in handle_callback_query: {e}")
            import traceback
            traceback.print_exc()
            if query_id:
                try:
                    self.bot.answerCallbackQuery(query_id, text="Unexpected error.", show_alert=True)
                except:
                    pass

    # --- MQTT Notification Handler ---
    def notify(self, topic, payload_bytes):
        """Handles incoming MQTT alert messages."""
        print(f"[MQTT] Received message on topic: {topic}")
        try:
            payload = json.loads(payload_bytes.decode('utf-8'))

            device_id = payload.get('device_id') or payload.get('bn') 
            user_id = payload.get('userID') 
            alert_message = payload.get('message', 'An event occurred.')
            alert_type_from_payload = payload.get('alert_type')
            alert_type_from_topic = topic.split('/')[-1]
            alert_type = alert_type_from_payload or alert_type_from_topic
            severity = payload.get('severity', 'info')

            target_chat_id = None

            if user_id:
                print(f"[ALERT] Alert received for user ID: {user_id}. Finding chat_id...")
                try:
                    user_info = self._catalog_get(f"/users/{user_id}")
                    target_chat_id = user_info.get('telegram_chat_id')
                    if target_chat_id: print(f"[ALERT] Found chat_id {target_chat_id} for user {user_id}")
                    else: print(f"[ALERT] User {user_id} found but no chat_id linked. Alert ignored.")
                except CatalogError as e: print(f"[ALERT] Failed to get user info for {user_id}: {e}")
                except Exception as e: print(f"[ALERT] Unexpected error finding chat_id for user {user_id}: {e}")
            elif device_id:
                print(f"[ALERT] Alert received for device: {device_id}. Finding assigned user...")
                try:
                    device_info = self._catalog_get(f"/devices/{device_id}")
                    if device_info.get('user_assigned') and device_info.get('assigned_user'):
                        assigned_user_id = device_info['assigned_user']
                        user_info = self._catalog_get(f"/users/{assigned_user_id}")
                        target_chat_id = user_info.get('telegram_chat_id')
                        if target_chat_id: print(f"[ALERT] Found chat_id {target_chat_id} for user {assigned_user_id} assigned to device {device_id}")
                        else: print(f"[ALERT] User {assigned_user_id} found for device {device_id} but no chat_id linked. Alert ignored.")
                    else: print(f"[ALERT] Device {device_id} is not assigned. Alert ignored.")
                except CatalogError as e: print(f"[ALERT] Failed to get device/user info for {device_id}: {e}")
                except Exception as e: print(f"[ALERT] Unexpected error finding user/chat_id for device {device_id}: {e}")
            else: print("[ALERT] Alert payload missing 'userID' or 'deviceID'. Cannot determine target.")

            if target_chat_id:
                now = time.time()
                alert_key_base = f"{target_chat_id}_{alert_type}"
                alert_key = f"{alert_key_base}_{device_id}" if device_id else alert_key_base

                last_time = self.last_alert_time.get(alert_key, 0)
                cooldown_sec = self.settings.get("defaults", {}).get("alert_cooldown_minutes", 15) * 60

                is_info_alert = alert_type.lower() == 'doorclosed'

                if not is_info_alert and (now - last_time < cooldown_sec):
                    print(f"[NOTIFY] Cooldown active for {alert_type} for user {target_chat_id}. Skipping.")
                    return

                try:
                    if alert_type.lower() == 'doorclosed':
                        icon = "🚪"
                        title = "Door Closed"
                        duration = payload.get('duration_seconds')
                        duration_text = f" after being open for {duration:.0f} seconds" if duration is not None else ""
                        telegram_msg = f"{icon} **{title}** {icon}\n\n"
                        if device_id: telegram_msg += f"**Device:** `{device_id}`\n"
                        telegram_msg += f"The fridge door was closed{duration_text}.\n"
                        telegram_msg += f"\n_Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
                        severity = 'info'
                    else:
                        icon = "🚨" if severity == "critical" else ("⚠️" if severity == "warning" else "ℹ️")
                        telegram_msg = f"{icon} **{alert_type.replace('_', ' ').upper()} Alert** {icon}\n\n"
                        if device_id: telegram_msg += f"**Device:** `{device_id}`\n"
                        telegram_msg += f"**Details:** {alert_message}\n"
                        if payload.get('recommended_action'): telegram_msg += f"**Suggestion:** {payload['recommended_action']}\n"
                        telegram_msg += f"\n_Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"

                    self.bot.sendMessage(int(target_chat_id), telegram_msg, parse_mode="Markdown")
                    print(f"[NOTIFY] Alert '{alert_type}' ({severity}) sent to user {target_chat_id}")

                    if not is_info_alert:
                        self.last_alert_time[alert_key] = now

                except ValueError: print(f"[NOTIFY] Invalid chat ID format: {target_chat_id}")
                except telepot.exception.BotWasBlockedError: print(f"[NOTIFY] Bot was blocked by user {target_chat_id}. Cannot send alert.")
                except telepot.exception.TelegramError as e: print(f"[NOTIFY] Failed to send alert to {target_chat_id}: {e}")
                except Exception as e: print(f"[NOTIFY] Unexpected error sending Telegram message: {e}")

        except json.JSONDecodeError: print(f"[MQTT] Received non-JSON payload on topic {topic}")
        except Exception as e: print(f"[ERROR] Unhandled exception in notify: {e}"); import traceback; traceback.print_exc()

    # --- Service Start/Run/Stop ---
    def start_telegram_loop(self):
        """Starts a custom polling loop to handle all update types including my_chat_member."""
        print("[INIT] Starting Telegram polling loop...")
        try:
            def polling_loop():
                """Custom polling loop that handles all Telegram update types."""
                offset = None
                print("[TELEGRAM] Polling loop started")

                while self.running:
                    try:
                        updates = self.bot.getUpdates(offset=offset, timeout=20)
                        for update in updates:
                            offset = update['update_id'] + 1
                            self.handle_telegram_update(update)
                    except Exception as e:
                        if self.running:
                            print(f"[ERROR] Error in polling loop: {e}")
                            import traceback
                            traceback.print_exc()
                            time.sleep(3)

                print("[TELEGRAM] Polling loop stopped")

            self.message_loop_thread = threading.Thread(target=polling_loop, daemon=True)
            self.message_loop_thread.start()
            print("[INIT] Telegram polling loop running.")
            return True

        except Exception as e:
            print(f"[ERROR] Failed to start Telegram loop: {e}")
            import traceback
            traceback.print_exc()
            return False

    def start(self):
        if not self.setup_mqtt():
            return False
        if not self.start_telegram_loop():
            if self.connected_mqtt:
                self.mqtt_client.stop()
            return False
        return True

    def run(self):
        print("=" * 60)
        print(f"    {self.service_info['serviceName']} v{self.service_info['version']}")
        print("=" * 60)
        if not self.start():
            self.stop()
            return
        if not self.register_with_catalog():
            print("[WARN] Failed initial registration with catalog.")
        reg_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        reg_thread.start()
        print("[INFO] Bot is running. Press CTRL+C to stop.")
        try:
            while self.running:
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] CTRL+C detected.")
        finally:
            self.stop()

    def stop(self):
        if not self.running:
            return
        print(f"[SHUTDOWN] Stopping {self.service_id}...")
        self.running = False
        if self.mqtt_client and self.connected_mqtt:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT client stopped.")
            except Exception as e:
                print(f"[SHUTDOWN] Error stopping MQTT client: {e}")
        time.sleep(1)
        print(f"[SHUTDOWN] {self.service_id} stopped.")

    # --- Standard Background Task ---
    def periodic_registration(self):
        interval = self.settings.get("catalog", {}).get("registration_interval_seconds", 300)
        print(f"[INFO] Periodic registration enabled every {interval} seconds.")
        while self.running:
            for _ in range(interval):
                if not self.running:
                    return
                time.sleep(1)
            if self.running:
                print(f"[REGISTER] Periodic re-registration...")
                self.register_with_catalog()


# --- Main Execution ---
if __name__ == "__main__":
    bot_service = None
    try:
        bot_service = TelegramBot(SETTINGS_FILE)
        bot_service.run()
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"[FATAL] Initialization failed: {e}")
    except Exception as e:
        print(f"[FATAL] An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if bot_service and bot_service.running:
            bot_service.stop()