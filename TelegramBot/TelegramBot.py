
import os
import re
import json
import time
import threading
from datetime import datetime, timezone

import requests
from requests.exceptions import ConnectionError, Timeout, RequestException
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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

        self.publish_topics = [] # To store the list of publishable topics
        self.config_set_template = None # To store the specific template we need

        self.bot = telepot.Bot(self.token)

        self.optimizer_base = "http://energy_optimization:8003"
        self.data_analysis_base = "http://data_analysis:8004"
        self.nodered_url = "http://nodered:1880/ui"


        # Commands, Callbacks, States
        self.commands = {
            "/start": {"handler": self.cmd_start, "help": "Start menu"},
            "/help": {"handler": self.cmd_help, "help": "Help & commands"},
            "/newdevice": {"handler": self.cmd_newdevice, "help": "Add a new device to your account."},
            "/mydevices": {"handler": self.cmd_mydevices, "help": "List your devices"},
            "/showme": {"handler": self.cmd_showme, "help": "Show account info"},
            "/settings": {"handler": self.cmd_settings, "help": "Change device settings"},
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
            "cb_settings_menu": {"handler": self.cb_settings_menu},
            "cb_service_menu": {"handler": self.cb_service_menu},
            "cb_show_current_info": {"handler": self.cb_show_current_info},
            "cb_service_modify": {"handler": self.cb_service_modify},
            "cb_change_value": {"handler": self.cb_change_value},
            "cb_edit_boolean": {"handler": self.cb_edit_boolean},
            "cb_set_boolean": {"handler": self.cb_set_boolean},
            "cb_back_to_settings": {"handler": self.cb_settings_menu},
            "cb_back_mydevices": {"handler": self.cb_back_mydevices},
            "cb_service_menu_back": {"handler": self.cb_service_menu_back},
            "cb_newdevice_start": {"handler": self.cb_newdevice_start}
        }
        self.status_handlers = {
            "waiting_for_mac": {"handler": self.handle_mac_input},
            "waiting_for_username": {"handler": self.handle_username_input},
            "waiting_for_newdevice_mac": {"handler": self.handle_newdevice_mac},
            "waiting_for_device_rename": {"handler": self.handle_device_rename_input},
            "waiting_for_new_value": {"handler": self.handle_new_value_input},
            "waiting_for_config": {"handler": None}, # Placeholder state, no handler needed
            "waiting_for_username_link": {"handler": self.handle_username_link}
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

    def _get_setting_details(self, field_name):
        """
        Helper function to get validation rules and user-friendly text for a setting.
        """
        # (Dati presi dalla tua immagine 'image_4fbd05.png')
        settings_map = {
            # Timer Configuration
            "max_door_open_seconds": {
                "name": "Door Open Timeout",
                "desc": "Maximum duration the door can remain open before triggering an alert.",
                "range_text": "(30-300 seconds)",
                "min": 30, "max": 300, "type": int
            },
            "check_interval": {
                "name": "Check Interval",
                "desc": "Frequency of monitoring checks for door violations.",
                "range_text": "(1-30 seconds)",
                "min": 1, "max": 30, "type": int
            },
            "enable_door_closed_alerts": {
                "name": "Door Closed Alerts",
                "desc": "Send notification when door closes after exceeding timeout.",
                "range_text": "(Enabled/Disabled)",
                "type": bool
            },
            # Spoilage Detection
            "gas_threshold_ppm": {
                "name": "Gas Level Threshold",
                "desc": "Gas concentration level that triggers spoilage alerts.",
                "range_text": "(100-1000 PPM)",
                "min": 100, "max": 1000, "type": int
            },
            "alert_cooldown_minutes": {
                "name": "Alert Cooldown Period",
                "desc": "Minimum time between consecutive alerts to prevent spam.",
                "range_text": "(5-120 minutes)",
                "min": 5, "max": 120, "type": int
            },
            "enable_continuous_alerts": {
                "name": "Alert Frequency",
                "desc": "Configure how and when spoilage alerts are triggered.",
                "range_text": "(On Breach Only / Continuous)",
                "type": bool,
                "true_text": "Continuous while above threshold",
                "false_text": "On Breach Only"
            },
            # Status Control
            "temp_min_celsius": {
                "name": "Minimum Temperature",
                "desc": "Acceptable temperature range for proper food preservation.",
                "range_text": "(-5 to 5 °C)",
                "min": -5, "max": 5, "type": float
            },
            "temp_max_celsius": {
                "name": "Maximum Temperature",
                "desc": "Acceptable temperature range for proper food preservation.",
                "range_text": "(5 to 15 °C)",
                "min": 5, "max": 15, "type": float
            },
            "humidity_max_percent": {
                "name": "Humidity Threshold",
                "desc": "Maximum humidity level before triggering malfunction alerts.",
                "range_text": "(50-95 %)",
                "min": 50, "max": 95, "type": float
            },
            "enable_malfunction_alerts": {
                "name": "Malfunction Alerts",
                "desc": "Control when malfunction alerts are sent.",
                "range_text": "(Enabled/Disabled)",
                "type": bool,
                "true_text": "Enabled", 
                "false_text": "Disabled"
            },
            "enable_door_closed_alerts": {
                "name": "Door Closed Alerts",
                "desc": "Send notification when door closes after exceeding timeout.",
                "range_text": "(Enabled/Disabled)",
                "type": bool,
                "true_text": "Enabled",
                "false_text": "Disabled"
            }
        }
        # Restituisce i dettagli o un dizionario vuoto se non trovato
        return settings_map.get(field_name, {"name": field_name, "desc": "", "range_text": "", "type": "unknown"})

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
        """Setup MQTT client and subscribe to topics from service endpoints"""
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            
            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            # --- MODIFY THIS SECTION ---
            # Extract and subscribe to topics from service endpoints
            subscribe_topics, self.publish_topics = self.extract_mqtt_topics() # <-- Capture publish_topics
            
            for topic in subscribe_topics:
                self.mqtt_client.mySubscribe(topic)
                self.subscribed_topics.append(topic) # This is good
                print(f"[MQTT] Subscribed to: {topic}")
            
            # Now, find and store the config template from the publish list
            for pub_topic in self.publish_topics:
                if "{service_name}" in pub_topic and "config_update" in pub_topic:
                    self.config_set_template = pub_topic
                    print(f"[MQTT] Found config publish template: {self.config_set_template}")
                    break
            
            if not self.config_set_template:
                print("[MQTT] WARNING: 'config_update' template not found in settings.json endpoints.")
            # --- END OF MODIFICATION ---

            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            return True
            
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

    # --- Telegram Command Handlers ---
    def cmd_start(self, chat_id, msg, *args):
        username = self._get_username(msg)
        self.bot.sendMessage(chat_id, f"👋 Welcome, {username}!")
        existing_user = self._is_registered(chat_id)
        if existing_user and existing_user.get("devicesList"):
            self.bot.sendMessage(chat_id, "You seem to be already set up.\nUse /mydevices or /help.")
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
    
    def cmd_newdevice(self, chat_id, msg, *args):
        user = self._is_registered(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are not registered yet. Use /start first.")
            return
        self.bot.sendMessage(
            chat_id,
            "Please enter the **MAC address** of the new fridge to link to your account.\n"
            "(Format: `XX:XX:XX:XX:XX:XX` or `AABBCC112233`)",
            parse_mode="Markdown"
        )
        self.set_status(chat_id, "waiting_for_newdevice_mac", user_id=user["userID"])

    def cmd_settings(self, chat_id, msg, *args):
        """
        Handler for the /settings command.
        Shows a list of devices that the user can configure.
        """
        user = self._is_registered(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are not registered yet. Use /start to begin.")
            return

        try:
            user_id = user["userID"]
            devices = user.get("devicesList", []) 
            
            if not devices:
                self.bot.sendMessage(chat_id, "You have no devices assigned to configure. Use /newdevice to add one.")
                return

            buttons = []
            for d in devices:
                device_name = d.get('deviceName', d.get('deviceID', 'Unknown'))
                device_id = d.get('deviceID')
                if device_id:
                    buttons.append(
                        [InlineKeyboardButton(text=f"⚙️ {device_name}",
                                            callback_data=f"cb_settings_menu {device_id}")]
                    )

            buttons.append([InlineKeyboardButton(text="« Close", callback_data="cb_quit_menu")])

            self.bot.sendMessage(
                chat_id, 
                "Select a device to configure:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )

        except Exception as e:
            self.bot.sendMessage(chat_id, f"⚠️ Failed to retrieve devices: {e}")

    def cmd_mydevices(self, chat_id, msg, *args):
        user = self._is_registered(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are not registered yet. Use /start to begin.")
            return
        try:
            user_id = user["userID"]
            devices = self._catalog_get(f"/users/{user_id}/devices")
            if not devices:
                self.bot.sendMessage(chat_id, "You have no devices yet. Use /newdevice to add one.")
                return

            # ✅ Each device gets its own button — nothing else in this view
            buttons = [
                [InlineKeyboardButton(text=f"🧊 {d.get('user_device_name', d.get('deviceID', 'Unknown'))}",
                                    callback_data=f"cb_device_menu {d.get('deviceID')}")]
                for d in devices
            ]
            buttons.append([InlineKeyboardButton(text="➕ Add new device", callback_data="cb_newdevice_start")])

            self.bot.sendMessage(chat_id, "Your Devices:",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        except Exception as e:
            self.bot.sendMessage(chat_id, f"⚠️ Failed to retrieve devices: {e}")

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
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        device_display = f"`{self.escape_markdown(device_id)}`"
        buttons = [
            # Queste funzioni (cb_device_info, ecc.) sono già definite nel tuo file
            [InlineKeyboardButton(text="ℹ️ Show Info", callback_data=f"cb_device_info {device_id}")],
            [InlineKeyboardButton(text="✏️ Rename Device", callback_data=f"cb_device_rename {device_id}")],
            
            # --- ECCO LA RIGA AGGIUNTA ---
            [InlineKeyboardButton(text="⚙️ Settings", callback_data=f"cb_settings_menu {device_id}")],
            # --- FINE ---
            
            [InlineKeyboardButton(text="❌ Unassign Device", callback_data=f"cb_device_unassign {device_id}")],
            [InlineKeyboardButton(text="« Back", callback_data="/mydevices")],
            [InlineKeyboardButton(text="Close Menu", callback_data="cb_quit_menu")]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Modifica il messaggio, non ne invia uno nuovo
        self.bot.editMessageText(
            msg_identifier, 
            f"Options for device {device_display}:", 
            reply_markup=keyboard, 
            parse_mode="Markdown"
        )

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

    def cb_device_unassign(self, query_id, chat_id, msg_query, *args):
        device_id = args[0]
        self.bot.answerCallbackQuery(query_id)
        
        # Ottieni l'ID del messaggio del menu
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        # Modifica il menu in "Loading..."
        self.bot.editMessageText(msg_identifier, f"🔄 Unassigning `{self.escape_markdown(device_id)}`...")

        try:
            response = self._catalog_post(f"/devices/{device_id}/unassign", data=None)
            
            # Modifica il "Loading..." nel messaggio di successo
            self.bot.editMessageText(msg_identifier, f"✅ Device `{self.escape_markdown(device_id)}` unassigned successfully.", parse_mode="Markdown")
            
            # (Opzionale) Invia un *nuovo* messaggio per guidare l'utente
            self.bot.sendMessage(chat_id, "Use /mydevices to see your updated list.")
            
        except CatalogError as e:
            # Modifica il "Loading..." nel messaggio di errore
            self.bot.editMessageText(msg_identifier, f"❌ Unassignment failed: {e}")

    def cb_device_rename(self, query_id, chat_id, msg_query, *args):
        """Callback to initiate device rename flow by editing the message."""
        device_id = args[0] if args else None
        if not device_id:
            self.bot.answerCallbackQuery(query_id, "Error: Missing device ID.")
            return

        self.bot.answerCallbackQuery(query_id)
        
        # 1. Ottieni l'ID del messaggio da modificare
        msg_identifier = telepot.message_identifier(msg_query['message'])

        try:
            # (Opzionale, ma consigliato) Recupera il nome attuale
            device_info = self._catalog_get(f"/devices/{device_id}")
            current_name = device_info.get('user_device_name', 'N/A')
            
            # 2. Modifica il messaggio precedente
            self.bot.editMessageText(
                msg_identifier,
                f"✏️ **Rename Device**\n\n"
                f"Device: `{self.escape_markdown(device_id)}`\n"
                f"Current name: *{self.escape_markdown(current_name)}*\n\n"
                f"Please send the new name for this device.\n"
                f"(Type /cancel to abort)",
                parse_mode="Markdown"
                # Non mettiamo una 'reply_markup' così i bottoni scompaiono
            )

            # 3. Imposta lo stato per attendere la risposta
            self.set_status(chat_id, "waiting_for_device_rename", 
                            device_id=device_id, 
                            old_name=current_name,
                            # Salva l'ID del messaggio per modificarlo di nuovo dopo!
                            msg_identifier=msg_identifier 
                           )

        except CatalogError as e:
            self.bot.editMessageText(
                msg_identifier,
                f"❌ Error fetching device info: {e}"
            )
    
    def cb_settings_menu(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        device_id = args[0]
        msg_identifier = telepot.message_identifier(msg_query['message'])

        text = f"⚙️ **Settings**\nSelect a service to configure for device `{device_id}`:"
        buttons = [
            [InlineKeyboardButton(text="⏱️ Door Timer", callback_data=f"cb_service_menu {device_id} TimerUsageControl")],
            [InlineKeyboardButton(text="🔥 Food Spoilage", callback_data=f"cb_service_menu {device_id} FoodSpoilageControl")],
            [InlineKeyboardButton(text="🌡️ Fridge Status", callback_data=f"cb_service_menu {device_id} FridgeStatusControl")],
            [InlineKeyboardButton(text="« Back to Device", callback_data=f"cb_device_menu {device_id}")]
        ]

        self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_service_menu(self, query_id, chat_id, msg_query, *args):
        """Requests the current config from the selected microservice."""
        self.bot.answerCallbackQuery(query_id)
        device_id, service_name = args[0], args[1]
        msg_identifier = telepot.message_identifier(msg_query['message'])

        if not self.config_set_template:
             self.bot.editMessageText(msg_identifier, "❌ Error: Bot is not configured for publishing. Please check settings.json.")
             return

        # --- SALVATAGGIO PULITO (singo annidamento) ---
        state_data_to_save = {
            "device_id": device_id,
            "service_name": service_name,
            "msg_identifier": msg_identifier
        }
        self.set_status(chat_id, "waiting_for_config", data=state_data_to_save)
        # --- FINE SALVATAGGIO ---

        # Request the config
        topic = self.config_set_template.format(service_name=service_name, device_id=device_id)
        payload = {"type": "config_get", "device_id": device_id}
        
        try:
            self.mqtt_client.myPublish(topic, payload)
            self.bot.editMessageText(msg_identifier, f"🔄 Fetching settings for *{service_name}*...", parse_mode="Markdown")
        except Exception as e:
            self.bot.editMessageText(msg_identifier, f"❌ Error requesting settings: {e}")
            self.clear_status(chat_id)
    
    def cb_show_current_info(self, query_id, chat_id, msg_query, *args):
        """Displays the current settings as text."""
        self.bot.answerCallbackQuery(query_id)
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        print("\n--- DEBUG: cb_show_current_info ---")
        state = self.get_status(chat_id)
        print(f"[DEBUG] Stato letto da get_status: {state}")

        # --- CORREZIONE LETTURA ANNIDATA ---
        if not state or not state.get("data") or not state.get("data").get("data"):
            print("[DEBUG] ERRORE: Stato non trovato o struttura 'data: {data: ...}' non presente.")
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired. Please start again.")
            print("-----------------------------------\n")
            return
            
        # Accedi al dizionario 'data' interno
        state_data = state.get("data").get("data", {})
        config = state_data.get("config")
        service_name = state_data.get("service_name")
        # --- FINE CORREZIONE ---

        if not config:
            print(f"[DEBUG] ERRORE: state['data']['data'].get('config') è None o vuoto.")
            self.bot.editMessageText(msg_identifier, "❌ Error: No config found in state. Please start again.")
            print("-----------------------------------\n")
            return
        
        print(f"[DEBUG] Trovata config: {config}")

        text = f"ℹ️ Current *{service_name}* Settings:\n\n"
        if not config:
            text += "_No settings found._"
        else:
            for key, value in config.items():
                key_formatted = key.replace("_", " ").capitalize()
                text += f"▪️ *{key_formatted}*: `{value}`\n"
        
        buttons = [
            [InlineKeyboardButton(text="« Back", callback_data=f"cb_service_menu_back")]
        ]
        
        self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        print("-----------------------------------\n")

    def handle_config_response(self, topic, payload):
        """
        Called by notify() to process incoming config messages.
        (FIXED: Handles nested state and saves clean state)
        """
        print("\n--- DEBUG: handle_config_response ---")
        device_id_from_payload = payload.get("device_id")
        topic_type = "UNKNOWN"
        if "config_data" in topic: topic_type = "config_data"
        if "config_ack" in topic: topic_type = "config_ack"
        if "config_error" in topic: topic_type = "config_error"

        print(f"[MQTT] Device ID: {device_id_from_payload} (Tipo: {type(device_id_from_payload)})")
        print(f"[MQTT] Topic Type: {topic_type}")

        if not device_id_from_payload:
            print("[DEBUG] Errore: Messaggio MQTT senza device_id. Uscita.")
            print("---------------------------------------\n")
            return

        chat_id_found = None
        state_found = None
        state_data = None # Qui salveremo i dati puliti

        print(f"[DEBUG] Controllo {len(self.user_states)} stati utente...")
        print(f"[DEBUG] Stato attuale completo: {self.user_states}")
        
        for chat_id, state in list(self.user_states.items()):
            current_state_str = state.get("state")
            
            # --- FUNZIONE "SCAVA" PER TROVARE I DATI ---
            # Questa logica naviga la struttura annidata {'data': {'data': ...}}
            temp_data = state.get("data", {})
            while "data" in temp_data: # Continua a scendere finché c'è un 'data'
                temp_data = temp_data.get("data", {})
            device_id_in_state = temp_data.get("device_id")
            # --- FINE LOGICA "SCAVA" ---

            print(f"[DEBUG] Utente: {chat_id}, Stato: {current_state_str}, Device nello stato: {device_id_in_state} (Tipo: {type(device_id_in_state)})")

            if current_state_str in ["waiting_for_config", "waiting_for_new_value"]:
                if device_id_in_state == device_id_from_payload:
                    print(f"[DEBUG] ---> TROVATO! Questo utente ({chat_id}) sta aspettando questo device.")
                    chat_id_found = chat_id
                    state_data = temp_data # Trovati i dati corretti!
                    break
                else:
                    print(f"[DEBUG] ---> SCARTATO. Stato OK, ma i device non combaciano.")
            else:
                print(f"[DEBUG] ---> SCARTATO. Stato non è 'waiting_...'.")

        if not chat_id_found:
            print(f"[DEBUG] ERRORE: Nessun utente trovato in attesa per {device_id_from_payload}. Messaggio scartato.")
            print("---------------------------------------\n")
            return
        
        print("[DEBUG] Procedo con la gestione del messaggio...")
        
        msg_identifier = state_data.get("msg_identifier")
        service_name = state_data.get("service_name")

        if not msg_identifier or not service_name:
            print(f"[DEBUG] ERRORE: Stato corrotto. 'msg_identifier' o 'service_name' mancanti. Stato: {state_data}")
            print("---------------------------------------\n")
            self.clear_status(chat_id_found)
            return

        # --- GESTIONE DEI MESSAGGI (con salvataggio pulito) ---

        if topic_type == "config_data":
            config = payload.get("config", {})
            state_data["config"] = config # Salva la config
            
            # --- SALVATAGGIO PULITO (singo annidamento) ---
            self.set_status(chat_id_found, "waiting_for_config", data=state_data)
            print(f"[DEBUG] Stato salvato (config_data): {self.get_status(chat_id_found)}")
            # --- FINE SALVATAGGIO ---
                                
            text = f"⚙️ **{service_name}** Settings\nSelect an option:"
            buttons = [
                [InlineKeyboardButton(text="ℹ️ Show Current Info", callback_data=f"cb_show_current_info")],
                [InlineKeyboardButton(text="✏️ Modify Settings", callback_data=f"cb_service_modify")],
                [InlineKeyboardButton(text="« Back to Services", callback_data=f"cb_settings_menu {device_id_from_payload}")]
            ]
            self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

        elif topic_type == "config_ack":
            self.bot.editMessageText(msg_identifier, "✅ Settings updated successfully!")
            self.clear_status(chat_id_found)

        elif topic_type == "config_error":
            error_msg = payload.get("error_message", "Unknown error")
            self.bot.editMessageText(msg_identifier, f"❌ Update failed: {error_msg}\n\nPlease try again.")
            
            # --- SALVATAGGIO PULITO (singo annidamento) ---
            self.set_status(chat_id_found, "waiting_for_config", data=state_data)
            print(f"[DEBUG] Stato ripristinato (config_error): {self.get_status(chat_id_found)}")
            # --- FINE SALVATAGGIO ---
            
        print("---------------------------------------\n")

    def cb_service_modify(self, query_id, chat_id, msg_query, *args):
        """Displays the specific settings that can be modified."""
        self.bot.answerCallbackQuery(query_id)
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        state = self.get_status(chat_id)
        
        # Legge lo stato annidato corretto
        if not state or not state.get("data") or not state.get("data").get("data"):
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired. Please start again.")
            return
            
        state_data = state.get("data").get("data", {})
        config = state_data.get("config", {})
        service_name = state_data.get("service_name")
        device_id = state_data.get("device_id")
        
        if not config or not service_name or not device_id:
             self.bot.editMessageText(msg_identifier, "❌ Error: State is corrupt. Please start again.")
             self.clear_status(chat_id)
             return

        text = f"✏️ Modify *{service_name}*\nSelect a setting to change:"
        buttons = []

        # --- MODIFICA LOGICA BOTTONI ---
        if service_name == "TimerUsageControl":
            # Numerici (invariati)
            buttons.append([InlineKeyboardButton(text=f"Max Door Open: {config.get('max_door_open_seconds', 'N/A')}s", callback_data="cb_change_value max_door_open_seconds")])
            buttons.append([InlineKeyboardButton(text=f"Check Interval: {config.get('check_interval', 'N/A')}s", callback_data="cb_change_value check_interval")])
            
            # --- MODIFICA LOGICA BOOLEANA ---
            field = 'enable_door_closed_alerts'
            details = self._get_setting_details(field)
            current_val_text = details.get("true_text", "Enabled") if config.get(field) else details.get("false_text", "Disabled")
            buttons.append([InlineKeyboardButton(text=f"{details['name']}: {current_val_text}", callback_data=f"cb_edit_boolean {field}")])
        
        elif service_name == "FoodSpoilageControl":
            # Numerici (invariati)
            buttons.append([InlineKeyboardButton(text=f"Gas Threshold: {config.get('gas_threshold_ppm', 'N/A')} PPM", callback_data="cb_change_value gas_threshold_ppm")])
            buttons.append([InlineKeyboardButton(text=f"Alert Cooldown: {config.get('alert_cooldown_minutes', 'N/A')} min", callback_data="cb_change_value alert_cooldown_minutes")])

            # --- MODIFICA LOGICA BOOLEANA (con testi personalizzati) ---
            field = 'enable_continuous_alerts'
            details = self._get_setting_details(field)
            # Questo ora userà "Continuous..." o "On Breach Only"
            current_val_text = details.get("true_text", "Enabled") if config.get(field) else details.get("false_text", "Disabled")
            buttons.append([InlineKeyboardButton(text=f"{details['name']}: {current_val_text}", callback_data=f"cb_edit_boolean {field}")])

        elif service_name == "FridgeStatusControl":
            # Numerici (invariati)
            buttons.append([InlineKeyboardButton(text=f"Min Temp: {config.get('temp_min_celsius', 'N/A')}°C", callback_data="cb_change_value temp_min_celsius")])
            buttons.append([InlineKeyboardButton(text=f"Max Temp: {config.get('temp_max_celsius', 'N/A')}°C", callback_data="cb_change_value temp_max_celsius")])
            buttons.append([InlineKeyboardButton(text=f"Max Humidity: {config.get('humidity_max_percent', 'N/A')}%", callback_data="cb_change_value humidity_max_percent")])
            
            # --- MODIFICA LOGICA BOOLEANA ---
            field = 'enable_malfunction_alerts'
            details = self._get_setting_details(field)
            current_val_text = details.get("true_text", "Enabled") if config.get(field) else details.get("false_text", "Disabled")
            buttons.append([InlineKeyboardButton(text=f"{details['name']}: {current_val_text}", callback_data=f"cb_edit_boolean {field}")])
        
        buttons.append([InlineKeyboardButton(text="« Back", callback_data=f"cb_service_menu_back")])
        
        self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_change_value(self, query_id, chat_id, msg_query, *args):
        """Asks the user to input the new value (with a nice description). (DEBUG)"""
        print("\n--- DEBUG: cb_change_value (Scrittore di Stato) ---")
        self.bot.answerCallbackQuery(query_id)
        field_name = args[0]
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        state = self.get_status(chat_id)
        print(f"[DEBUG] 1. Stato letto da get_status: {state}")

        # --- LOGICA ROBUSTA PER LEGGERE LO STATO ANNIDATO ---
        if not state or not state.get("data"):
            print("[DEBUG] ERRORE: 'state' o 'state.data' non trovato.")
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired (state_data).")
            print("-----------------------------------\n")
            return

        state_data = state.get("data", {})
        
        # Gestisce il doppio annidamento
        if "data" in state_data:
            print("[DEBUG] 2. Trovata struttura doppiamente annidata. Scavo più a fondo.")
            state_data = state_data.get("data", {})
        else:
            print("[DEBUG] 2. Trovata struttura singolarmente annidata.")

        print(f"[DEBUG] 3. Dati estratti (state_data): {state_data}")
        # --- FINE LOGICA LETTURA ---

        # Prepara i dati da salvare (copiando solo quelli puliti)
        data_to_save = {
            "device_id": state_data.get("device_id"),
            "service_name": state_data.get("service_name"),
            "msg_identifier": state_data.get("msg_identifier"),
            "config": state_data.get("config", {}),
            "field_name": field_name # Aggiunge il nuovo campo
        }
        
        if not data_to_save["device_id"]:
             print(f"[DEBUG] ERRORE: 'device_id' non trovato nei dati estratti!")
             self.bot.editMessageText(msg_identifier, "❌ Error: Session state is corrupt (device_id).")
             print("-----------------------------------\n")
             return

        # Salva lo stato in modo PULITO (singolo annidamento)
        self.set_status(chat_id, "waiting_for_new_value", data=data_to_save)
        print(f"[DEBUG] 4. Stato salvato in 'waiting_for_new_value': {self.get_status(chat_id)}")
        
        # Messaggio user-friendly (come richiesto)
        details = self._get_setting_details(field_name)
        text = (
            f"✏️ **{details['name']}**\n\n"
            f"_{details['desc']}_\n\n"
            f"Please enter a new value {details['range_text']}.\n"
            f"(Type /cancel to abort)"
        )
        self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown")
        print("-----------------------------------\n")

    def cb_show_service_options(self, chat_id, msg_identifier, state_data):
        """
        Helper function to show the 'Show Info' / 'Modify' menu.
        This is the central menu for a service.
        """
        service_name = state_data.get("service_name", "Unknown Service")
        device_id = state_data.get("device_id")

        text = f"⚙️ **{service_name}** Settings\nSelect an option:"
        buttons = [
            [InlineKeyboardButton(text="ℹ️ Show Current Info", callback_data=f"cb_show_current_info")],
            [InlineKeyboardButton(text="✏️ Modify Settings", callback_data=f"cb_service_modify")],
            [InlineKeyboardButton(text="« Back to Services", callback_data=f"cb_settings_menu {device_id}")]
        ]
        
        try:
            self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        except telepot.exception.TelegramError as e:
            print(f"Error editing message, sending new one: {e}")
            self.bot.sendMessage(chat_id, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def handle_new_value_input(self, chat_id, msg, state_data_wrapper):
        """Receives the new value, validates it, and sends the MQTT update. (DEBUG)"""
        print("\n--- DEBUG: handle_new_value_input (Lettore di Stato) ---")
        
        # 1. Logga lo stato che riceve
        print(f"[DEBUG] 1. Stato ricevuto (state_data_wrapper): {state_data_wrapper}")

        # --- LETTURA STATO PULITO (singolo annidamento) ---
        if not state_data_wrapper.get("data"):
            self.bot.sendMessage(chat_id, "❌ Error: Session expired (corrupt state). Please start again.")
            self.clear_status(chat_id)
            print("[DEBUG] ERRORE: 'data' non trovato in state_data_wrapper.")
            print("-----------------------------------\n")
            return

        # Questo è il dizionario corretto
        state_data = state_data_wrapper.get("data", {})
        print(f"[DEBUG] 2. Dati estratti (state_data): {state_data}")
        # --- FINE LETTURA ---

        service_name = state_data.get("service_name")
        device_id = state_data.get("device_id")
        field_name = state_data.get("field_name")
        msg_identifier = state_data.get("msg_identifier")
        
        # 3. Logga le variabili
        print(f"[DEBUG] 3. Variabili lette:")
        print(f"[DEBUG]    service_name: {service_name}")
        print(f"[DEBUG]       device_id: {device_id}")
        print(f"[DEBUG]      field_name: {field_name}")
        print(f"[DEBUG]  msg_identifier: {msg_identifier}")
        
        # 4. Il controllo che falliva
        if not all([service_name, device_id, field_name, msg_identifier]):
            self.bot.sendMessage(chat_id, "❌ Error: Session expired (missing data). Please start again.")
            self.clear_status(chat_id)
            print("[DEBUG] ERRORE: 'if not all(...)' è fallito. Dati mancanti.")
            print("-----------------------------------\n")
            return
        
        print("[DEBUG] 4. Controllo 'not all' superato.")
        
        new_value_str = msg.get("text", "").strip()
        new_value = None
        details = self._get_setting_details(field_name)

        # --- 5. VALIDAZIONE (come da tua richiesta) ---
        try:
            if details["type"] == bool:
                if new_value_str.lower() in ['true', '1', 'on', 'enabled', 'yes', 'activate']: new_value = True
                elif new_value_str.lower() in ['false', '0', 'off', 'disabled', 'no', 'deactivate']: new_value = False
                else: raise ValueError(f"Invalid value. Use 'Enabled' or 'Disabled'.")
            else:
                new_value = float(new_value_str)
                if new_value.is_integer(): new_value = int(new_value_str)
                if "min" in details and new_value < details["min"]: raise ValueError(f"Value must be {details['min']} or higher.")
                if "max" in details and new_value > details["max"]: raise ValueError(f"Value must be {details['max']} or lower.")
                if field_name == 'temp_min_celsius':
                    current_max = state_data.get("config", {}).get("temp_max_celsius", 15)
                    if new_value >= current_max: raise ValueError(f"Min Temp ({new_value}°C) must be lower than Max Temp ({current_max}°C).")
                if field_name == 'temp_max_celsius':
                    current_min = state_data.get("config", {}).get("temp_min_celsius", -5)
                    if new_value <= current_min: raise ValueError(f"Max Temp ({new_value}°C) must be higher than Min Temp ({current_min}°C).")
        except ValueError as e:
            self.bot.sendMessage(chat_id, f"⚠️ Invalid value: {e}\nPlease try again or type /cancel.")
            print(f"[DEBUG] ERRORE Validazione: {e}")
            print("-----------------------------------\n")
            return 
        
        print(f"[DEBUG] 5. Validazione superata. Nuovo valore: {new_value}")
        
        if not self.config_set_template:
             self.bot.sendMessage(chat_id, "❌ Error: Bot is not configured for publishing.")
             self.clear_status(chat_id)
             print("[DEBUG] ERRORE: config_set_template non trovato.")
             print("-----------------------------------\n")
             return

        # Costruisci e invia il messaggio MQTT
        topic = self.config_set_template.format(service_name=service_name, device_id=device_id)
        payload = { "type": "device_config_update", "device_id": device_id, "config": { field_name: new_value } }
        
        try:
            self.mqtt_client.myPublish(topic, payload)
            self.bot.editMessageText(msg_identifier, f"🔄 Sending update: *{details['name']}* = *{new_value_str}*...", parse_mode="Markdown")
            
            # Prepariamo i dati per ripristinare lo stato (pulito)
            state_to_save = state_data.copy()
            if "field_name" in state_to_save:
                del state_to_save["field_name"]
            
            self.set_status(chat_id, "waiting_for_config", data=state_to_save)
            print(f"[DEBUG] 6. Stato ripristinato a 'waiting_for_config': {self.get_status(chat_id)}")
            
        except Exception as e:
            print(f"[ERROR] Errore durante il publish o editMessageText: {e}")
            self.bot.sendMessage(chat_id, f"❌ An error occurred while sending the update: {e}")
            self.clear_status(chat_id)
            
        print("-----------------------------------\n")

    def cb_back_mydevices(self, query_id, chat_id, msg_query, *args):
        self.cmd_mydevices(chat_id, msg_query)

    def cb_newdevice_start(self, query_id, chat_id, msg_query, *args):
        self.cmd_newdevice(chat_id, msg_query)
    
    def cb_device_info(self, query_id, chat_id, msg_query, *args):
        """
        Callback: Retrieves detailed device info and *edits* the current message.
        """
        self.bot.answerCallbackQuery(query_id)
        
        # --- 1. ESTRAI GLI ARGOMENTI CORRETTI ---
        try:
            device_id = args[0]
        except IndexError:
            print("[ERROR] cb_device_info called without device_id")
            return
            
        # Ottieni l'ID del messaggio da modificare (quello con i bottoni)
        msg_identifier = telepot.message_identifier(msg_query['message'])

        # --- 2. MODIFICA IL MESSAGGIO IN "LOADING..." ---
        # Questo fa scomparire il menu precedente
        try:
            self.bot.editMessageText(
                msg_identifier, 
                f"⏳ Retrieving info for `{self.escape_markdown(device_id)}`...",
                parse_mode="Markdown"
            )
        except telepot.exception.TelegramError as e:
            print(f"[WARN] Failed to edit message to 'loading': {e}")
            # L'utente ha cliccato troppo velocemente? Non importa, andiamo avanti.

        # --- 3. LOGICA DI FETCH E VISUALIZZAZIONE ---
        try:
            device = self._catalog_get(f"/devices/{device_id}")
            if not device:
                self.bot.editMessageText(
                    msg_identifier,
                    f"⚠️ No data found for device `{self.escape_markdown(device_id)}`.",
                    parse_mode="Markdown"
                )
                return

            timestamp_str = datetime.now(timezone.utc).isoformat()
            name = device.get("user_device_name", "N/A")
            mac = device.get("mac_address", "N/A")
            assigned = "✅ Yes" if device.get("user_assigned", False) else "❌ No"
            status = device.get("status", "Unknown")

            msg = (
                f"📘 *Device Information*\n\n"
                f"🆔 *ID:* `{self.escape_markdown(device_id)}`\n"
                f"🏷️ *Name:* {name}\n"
                f"🔢 *MAC:* `{mac}`\n"
                f"👤 *Assigned:* {assigned}\n"
                f"📡 *Status:* {status}\n\n"
                f"🕒 _Last update: {timestamp_str}_"
            )

            # --- 4. CREA IL BOTTONE "BACK" ---
            buttons = [
                # Questo bottone richiama 'cb_device_menu' con lo stesso device_id
                [InlineKeyboardButton(text="« Back", callback_data=f"cb_device_menu {device_id}")]
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            # --- 5. MODIFICA FINALE ---
            # Sostituisce il "Loading..." con le info E il bottone "Back"
            self.bot.editMessageText(
                msg_identifier,
                msg,
                parse_mode="Markdown",
                reply_markup=keyboard
            )

        except Exception as e:
            print(f"[ERROR] cb_device_info: {e}")
            self.bot.editMessageText(
                msg_identifier,
                f"⚠️ Failed to retrieve device info: {e}",
                parse_mode="Markdown"
            )

    def cb_service_menu_back(self, query_id, chat_id, msg_query, *args):
        """Goes back to the 'Show/Modify' menu. (FIXED)"""
        print("\n--- DEBUG: cb_service_menu_back ---")
        self.bot.answerCallbackQuery(query_id)
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        state = self.get_status(chat_id)
        print(f"[DEBUG] Stato letto da get_status: {state}")

        # --- CORREZIONE LETTURA STATO ANNIDATO ---
        # "Scava" per trovare i dati corretti
        state_data = {}
        if not state or not state.get("data"):
            print("[DEBUG] ERRORE: Stato non trovato o 'data' mancante.")
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired. Please start again.")
            print("-----------------------------------\n")
            return

        temp_data = state.get("data", {})
        while "data" in temp_data: # Gestisce il doppio (o triplo!) annidamento
            print("[DEBUG] Trovato 'data' annidato, scavo più a fondo...")
            temp_data = temp_data.get("data", {})
        
        state_data = temp_data # Trovati i dati corretti
        print(f"[DEBUG] Dati estratti (state_data): {state_data}")
        # --- FINE CORREZIONE ---
        
        if not state_data.get("device_id"):
             print(f"[DEBUG] ERRORE: 'device_id' non trovato nei dati estratti!")
             self.bot.editMessageText(msg_identifier, "❌ Error: Session state is corrupt (device_id).")
             print("-----------------------------------\n")
             return

        # Chiama la funzione che mostra il menu "Show/Modify"
        # passando i dati puliti (state_data)
        self.cb_show_service_options(chat_id, msg_identifier, state_data)
        print("-----------------------------------\n")

    def cb_edit_boolean(self, query_id, chat_id, msg_query, *args):
        """Shows the custom 'True' / 'False' buttons for a boolean setting."""
        self.bot.answerCallbackQuery(query_id)
        field_name = args[0]
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        state = self.get_status(chat_id)
        if not state or not state.get("data") or not state.get("data").get("data"):
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired. Please start again.")
            return
        state_data = state.get("data").get("data", {})
        config = state_data.get("config", {})
        current_val = config.get(field_name, False)
        
        # --- MODIFICA QUI ---
        # Ottieni i testi personalizzati dall'helper
        details = self._get_setting_details(field_name)
        true_text = f"✅ {details.get('true_text', 'Activate')}"
        false_text = f"❌ {details.get('false_text', 'Deactivate')}"
        current_val_text = details.get("true_text", "Enabled") if current_val else details.get("false_text", "Disabled")
        
        text = f"Change *{details['name']}*?\n\n_{details['desc']}_\n\nCurrently: `{current_val_text}`"
        
        buttons = [
            [
                InlineKeyboardButton(text=true_text, callback_data=f"cb_set_boolean {field_name} True"),
                InlineKeyboardButton(text=false_text, callback_data=f"cb_set_boolean {field_name} False")
            ],
            [InlineKeyboardButton(text="« Back to Modify", callback_data=f"cb_service_modify")]
        ]
        # --- FINE MODIFICA ---
        
        self.bot.editMessageText(msg_identifier, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_set_boolean(self, query_id, chat_id, msg_query, *args):
        """Sends the MQTT update for a boolean toggle."""
        self.bot.answerCallbackQuery(query_id)
        field_name, new_value_str = args[0], args[1]
        msg_identifier = telepot.message_identifier(msg_query['message'])
        
        # Converte la stringa "True" o "False" in un vero booleano
        new_value = (new_value_str.lower() == 'true')
        
        # Recupera i dati di stato per l'invio
        state = self.get_status(chat_id)
        if not state or not state.get("data") or not state.get("data").get("data"):
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired. Please start again.")
            return
        state_data = state.get("data").get("data", {})

        service_name = state_data.get("service_name")
        device_id = state_data.get("device_id")
        
        if not all([service_name, device_id, field_name]):
            self.bot.editMessageText(msg_identifier, "❌ Error: Session expired (missing data). Please start again.")
            return

        if not self.config_set_template:
             self.bot.editMessageText(msg_identifier, "❌ Error: Bot is not configured for publishing.")
             return

        # Costruisce e invia il messaggio MQTT
        topic = self.config_set_template.format(service_name=service_name, device_id=device_id)
        payload = {
            "type": "device_config_update",
            "device_id": device_id,
            "config": {
                field_name: new_value
            }
        }
        
        try:
            self.mqtt_client.myPublish(topic, payload)
            
            new_val_text = "Activated" if new_value else "Deactivated"
            self.bot.editMessageText(msg_identifier, f"🔄 Sending update: *{field_name}* = *{new_val_text}*...", parse_mode="Markdown")
            
            # Reimposta lo stato a "waiting_for_config" per ricevere l'ack
            # Usa la stessa struttura annidata per coerenza
            self.set_status(chat_id, "waiting_for_config", data=state_data)
            
        except Exception as e:
            self.bot.editMessageText(msg_identifier, f"❌ Failed to send update: {e}")


    # --- Telegram State Handlers ---
    def handle_mac_input(self, chat_id, msg, state_data):
        """
        REVISED FLOW (con gestione account esistente):
        1) Valida MAC + cerca dispositivo
        2) Se assegnato:
             - Cerca se questo chat_id è già linkato a un utente.
             - A) Se chat_id è linkato E l'utente corrisponde -> Login OK ("Bentornato")
             - B) Se chat_id è linkato E l'utente NON corrisponde -> Errore ("Questo chat è di 'userB', il device è di 'userA'")
             - C) Se chat_id NON è linkato -> Chiedi username per verifica ("Il device è di 'userA'. Sei tu? Inserisci username")
        3) Se libero:
             - Cerca se questo chat_id è già linkato.
             - A) Se chat_id è linkato -> Assegna device a utente esistente.
             - B) Se chat_id NON è linkato -> Chiedi username per NUOVA registrazione.
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
            assigned_user = device_info.get('owner') # Questo è lo userID (es. "luca")

            # Controlla se questo chat_id è GIA' linkato a QUALSIASI utente
            linked_user_for_chat = self._is_registered(chat_id) # Ritorna user_dict o None

            if is_assigned:
                # --- CASO: IL DISPOSITIVO È GIÀ ASSEGNATO ---
                
                if linked_user_for_chat:
                    # Sottocaso A: Chat già linkato
                    linked_user_id = linked_user_for_chat['userID']
                    
                    if str(assigned_user).lower() == linked_user_id.lower():
                        # A.1) Chat linkato all'utente corretto -> LOGIN OK
                        self.bot.editMessageText(
                            telepot.message_identifier(processing_msg),
                            f"✅ Welcome back! The device `{self.escape_markdown(device_id)}` is already linked to your account.\nUse /mydevices to manage it.",
                            parse_mode="Markdown"
                        )
                        self.clear_status(chat_id)
                        return
                    else:
                        # A.2) Chat linkato a utente DIVERSO -> ERRORE
                        self.bot.editMessageText(
                            telepot.message_identifier(processing_msg),
                            f"⛔️ This device is assigned to user **{assigned_user}**, but your Telegram account is linked to user **{linked_user_id}**.\nPlease /deleteme and retry if you want to change accounts.",
                            parse_mode="Markdown"
                        )
                        self.clear_status(chat_id)
                        return
                
                else:
                    # Sottocaso B: Chat NON linkato (Il tuo caso!) -> CHIEDI USERNAME PER VERIFICA
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"ℹ️ This device is already assigned.\n\nIf this is you, please enter your username to link this Telegram chat to your account.\n(Type /cancel to stop)",
                        parse_mode="Markdown"
                    )
                    # Imposta il nuovo stato per la verifica
                    self.set_status(chat_id, "waiting_for_username_link", device_id=device_id, expected_user=assigned_user)
                    return

            else:
                # --- CASO: IL DISPOSITIVO È LIBERO ---
                
                if linked_user_for_chat:
                    # A) Chat già linkato -> Assegna device a utente ESISTENTE
                    user_id = linked_user_for_chat['userID']
                    username = linked_user_for_chat['userName']

                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Existing account '{user_id}' found. Linking device `{self.escape_markdown(device_id)}`...",
                        parse_mode="Markdown"
                    )

                    assign_response = self._catalog_post(f"/users/{user_id}/assign-device", {
                        "device_id": device_id,
                        "device_name": f"{username}'s Fridge"
                    })
                    final_name = assign_response.get("device", {}).get('user_device_name', f"{username}'s Fridge")

                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Device `{self.escape_markdown(device_id)}` successfully linked to your account as '{self.escape_markdown(final_name)}'. Use /mydevices to manage your devices",
                        parse_mode="Markdown"
                    )
                    self.clear_status(chat_id)
                    return

                else:
                    # B) Chat NON linkato -> Procedi con NUOVA REGISTRAZIONE
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Valid MAC address: `{self.escape_markdown(device_id)}` found and available!\n\n"
                        "This Telegram account is not yet registered. Please send your desired **username** to complete the registration (it will serve as your unique ID).",
                        parse_mode="Markdown"
                    )
                    self.set_status(chat_id, "waiting_for_username", mac_address=mac_input, device_id=device_id)


        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(processing_msg), f"❌ Operation failed: {e}")
            self.clear_status(chat_id)
        except Exception as e:
            print(f"[ERROR] Unexpected error in handle_mac_input: {e}")
            self.bot.editMessageText(telepot.message_identifier(processing_msg), "❌ Unexpected error.")
            self.clear_status(chat_id)
            import traceback; traceback.print_exc()
    
    def handle_newdevice_mac(self, chat_id, msg, state_data):
        """
        Handles the MAC address input when adding a *new device* to an existing user.
        The flow mirrors handle_mac_input but skips user registration logic.
        """
        mac_input = (msg.get("text") or "").strip()
        user_id = state_data.get("user_id")

        # --- Step 1: Validate MAC ---
        if not is_valid_mac(mac_input):
            self.bot.sendMessage(
                chat_id,
                "⚠️ Invalid MAC address format. Please use `XX:XX:XX:XX:XX:XX` or `AABBCC112233`.\nType /cancel to stop.",
                parse_mode="Markdown"
            )
            return

        processing_msg = self.bot.sendMessage(chat_id, f"🔎 Checking MAC `{mac_input}`...")

        try:
            # --- Step 2: Find device by MAC ---
            device_info = self._find_device_by_mac(mac_input)
            if not device_info:
                self.bot.editMessageText(
                    telepot.message_identifier(processing_msg),
                    f"❌ Error: MAC `{mac_input}` not found.\nPlease check it and try again using /newdevice.",
                    parse_mode="Markdown"
                )
                self.clear_status(chat_id)
                return

            device_id = device_info["deviceID"]
            is_assigned = device_info.get("user_assigned", False)
            assigned_user = device_info.get("owner")

            # --- Step 3: If device is already assigned ---
            if is_assigned:
                if assigned_user and str(assigned_user).lower() == str(user_id).lower():
                    # Device already belongs to this same user
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"✅ Device `{self.escape_markdown(device_id)}` is already linked to your account.",
                        parse_mode="Markdown"
                    )
                else:
                    # Device assigned to another user
                    self.bot.editMessageText(
                        telepot.message_identifier(processing_msg),
                        f"⛔️ This device is already assigned to another user (`{assigned_user}`).",
                        parse_mode="Markdown"
                    )
                self.clear_status(chat_id)
                return

            # --- Step 4: Device is free → assign to this existing user ---
            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                f"✅ Device `{self.escape_markdown(device_id)}` found and available.\nLinking it to your account...",
                parse_mode="Markdown"
            )

            assign_response = self._catalog_post(
                f"/users/{user_id}/assign-device",
                {
                    "device_id": device_id,
                    "device_name": f"{user_id}'s Fridge"
                }
            )

            final_name = assign_response.get("device", {}).get("user_device_name", f"{user_id}'s Fridge")

            self.bot.editMessageText(
                telepot.message_identifier(processing_msg),
                f"✅ Device `{self.escape_markdown(device_id)}` successfully added to your account as *{self.escape_markdown(final_name)}*.\nUse /mydevices to manage it.",
                parse_mode="Markdown"
            )
            self.clear_status(chat_id)
            return

        except CatalogError as e:
            self.bot.editMessageText(telepot.message_identifier(processing_msg), f"❌ Operation failed: {e}")
            self.clear_status(chat_id)
        except Exception as e:
            print(f"[ERROR] Unexpected error in handle_newdevice_mac: {e}")
            self.bot.editMessageText(telepot.message_identifier(processing_msg), "❌ Unexpected error.")
            self.clear_status(chat_id)
            import traceback; traceback.print_exc()

    def find_chat_id_by_device_id(self, device_id):
        """Helper function to find a user's chat_id from a device_id."""
        # This function calls the catalog to find the user, then their chat_id
        try:
            device_info = self._cat_get(f"/devices/{device_id}")
            if device_info.get('user_assigned') and device_info.get('assigned_user'):
                user_id = device_info['assigned_user']
                user_info = self._cat_get(f"/users/{user_id}")
                return user_info.get('telegram_chat_id')
            return None
        except Exception as e:
            print(f"[ERROR] Catalog call failed in find_chat_id_by_device_id: {e}")
            return None

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

    def handle_username_link(self, chat_id, msg, state_data):
        """
        Gestisce l'inserimento dell'username per collegare un chat_id
        a un account esistente che possiede già un dispositivo.
        """
        input_username = (msg.get("text") or "").strip()
        expected_user = state_data.get("expected_user")
        
        if not input_username:
            self.bot.sendMessage(chat_id, "Please enter your username or type /cancel.")
            return
            
        if not expected_user:
            self.bot.sendMessage(chat_id, "Internal error: expected user not found. Operation cancelled.")
            self.clear_status(chat_id)
            return

        # Confronta l'username inserito con quello atteso
        if input_username.lower() == expected_user.lower():
            # Successo! L'utente è corretto.
            processing_msg = self.bot.sendMessage(chat_id, f"✅ Username verified! Linking this chat to the '{expected_user}' account...")
            
            try:
                # Chiamiamo l'endpoint del Catalog per collegare il chat_id
                self._cat_post(f"/users/{expected_user}/link_telegram", {"chat_id": str(chat_id)})
                
                self.bot.editMessageText(
                    telepot.message_identifier(processing_msg),
                    f"✅ Success! Your Telegram account is now linked to **{expected_user}**.\nUse /mydevices to see your devices.",
                    parse_mode="Markdown"
                )
                self.clear_status(chat_id)
                
            except CatalogError as e:
                self.bot.editMessageText(telepot.message_identifier(processing_msg), f"❌ Linking failed: {e}")
                self.clear_status(chat_id)
            except Exception as e:
                self.bot.editMessageText(telepot.message_identifier(processing_msg), f"❌ An unexpected error occurred: {e}")
                self.clear_status(chat_id)
                
        else:
            # Username errato
            self.bot.sendMessage(
                chat_id,
                f"❌ Incorrect username. You entered '{input_username}', but the device is assigned to '{expected_user}'.\nOperation cancelled.",
                parse_mode="Markdown"
            )
            self.clear_status(chat_id)

    def handle_device_rename_input(self, chat_id, msg, state_data):
        """Handle new device name input and edits the message."""
        new_name = msg.get("text", "").strip()
        device_id = state_data.get("device_id")
        old_name = state_data.get("old_name")
        
        # Recupera l'ID del messaggio che stavamo modificando
        msg_identifier = state_data.get("msg_identifier")

        if not msg_identifier:
             # Fallback se l'ID del messaggio non è stato salvato nello stato
             self.bot.sendMessage(chat_id, "❌ Error: Session expired. Please try again.")
             self.clear_status(chat_id)
             return

        if not new_name:
            self.bot.sendMessage(chat_id, "⚠️ The device name cannot be empty. Please try again or type /cancel.")
            return # Rimane nello stato di attesa

        if len(new_name) > 50:
            self.bot.sendMessage(chat_id, "⚠️ The name is too long (max 50 characters). Please try again or type /cancel.")
            return # Rimane nello stato di attesa

        try:
            # Chiama l'API del Catalog
            self._cat_post(f"/devices/{device_id}/rename", {"user_device_name": new_name})
            
            # Modifica il messaggio "Please send..." con il successo
            self.bot.editMessageText(
                msg_identifier,
                f"✅ Device renamed successfully!\n\n"
                f"Previous name: *{self.escape_markdown(old_name)}*\n"
                f"New name: *{self.escape_markdown(new_name)}*",
                parse_mode="Markdown"
            )
            
            self.bot.sendMessage(chat_id, "Use /mydevices to view your updated devices list.")
            self.clear_status(chat_id)

        except CatalogError as e:
            # Modifica il messaggio "Please send..." con l'errore
            self.bot.editMessageText(
                msg_identifier,
                f"❌ Rename failed: {e}"
            )
            self.clear_status(chat_id)
        except Exception as e:
            print(f"[ERROR] Unexpected error in handle_device_rename_input: {e}")
            self.bot.editMessageText(
                msg_identifier,
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

            if "config_data" in topic or "config_ack" in topic or "config_error" in topic:
                self.handle_config_response(topic, payload)
                return

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
                    if device_info.get('user_assigned') and device_info.get('owner'):
                        assigned_user_id = device_info['owner']
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

                # if not is_info_alert and (now - last_time < cooldown_sec):
                #     print(f"[NOTIFY] Cooldown active for {alert_type} for user {target_chat_id}. Skipping.")
                #     return

                try:
                    if alert_type.lower() == 'doorclosed':
                        icon = "🚪"
                        title = "Door Closed"
                        duration = payload.get('duration_seconds')
                        duration_text = f" after being open for {duration:.0f} seconds" if duration is not None else ""
                        telegram_msg = f"{icon} **{title}** {icon}\n\n"
                        if device_id: telegram_msg += f"**Device:** `{device_id}`\n"
                        telegram_msg += f"The fridge door was closed{duration_text}.\n"
                        telegram_msg += f"\n_Timestamp: {datetime.now(timezone.utc).isoformat()}_"
                        severity = 'info'
                    else:
                        icon = "🚨" if severity == "critical" else ("⚠️" if severity == "warning" else "ℹ️")
                        telegram_msg = f"{icon} **{alert_type.replace('_', ' ').upper()} Alert** {icon}\n\n"
                        if device_id: telegram_msg += f"**Device:** `{device_id}`\n"
                        telegram_msg += f"**Details:** {alert_message}\n"
                        if payload.get('recommended_action'): telegram_msg += f"**Suggestion:** {payload['recommended_action']}\n"
                        telegram_msg += f"\n_Timestamp: {datetime.now(timezone.utc).isoformat()}_"

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