import time
import json
import threading
import telepot
import requests
from datetime import datetime, timezone

from MyMQTT import MyMQTT
from bot_utils import load_settings
from catalog_client import CatalogClient
from telegram_handlers import BotHandlers

SETTINGS_FILE = "settings.json"

def set_bot_descriptions(token: str, enable: bool = True):
    """Sets the bot's description and short description on Telegram."""
    if not enable or not token: return
    base = f"https://api.telegram.org/bot{token}"
    short = "SmartChill – Keep your fridge under control." # what is seen on the bio
    desc = "Welcome to SmartChill!\nMonitor your fridge, get alerts, and cut waste.\n\n• 🔐 Login or register\n• 📣 Real-time alerts\n• 🧰 Device management"
    try:
        requests.post(f"{base}/setMyShortDescription", data={"short_description": short}, timeout=5)
        requests.post(f"{base}/setMyDescription", data={"description": desc}, timeout=5)
        print("[DESC] Bot descriptions set")
    except Exception as e:
        print(f"[DESC] Failed to set descriptions: {e}")

class TelegramBotService:
    def __init__(self, settings_file=SETTINGS_FILE):
        # 1. Load Settings
        self.settings = load_settings(settings_file)
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        
        # 2. Init Components
        self.catalog = CatalogClient(self.settings["catalog"]["url"])
        
        self.token = self.settings["telegram"]["TOKEN"]
        self.bot = telepot.Bot(self.token)
        
        # MQTT Init
        self.mqtt_cfg = self.settings["mqtt"]
        client_id = f"{self.mqtt_cfg.get('clientID_prefix', 'tg_bot')}_{int(time.time())}"
        self.mqtt_client = MyMQTT(client_id, self.mqtt_cfg["brokerIP"], self.mqtt_cfg["brokerPort"], self)
        self.connected_mqtt = False
        
        # 3. Parse Endpoints for Topics
        self.subscribe_topics = []
        self.config_template = None
        self._parse_endpoints()
        
        # 4. Init Logic Handlers
        self.handlers = BotHandlers(self.bot, self.catalog, self.mqtt_client, self.config_template)
        
        # 5. State & Threading
        self.running = True
        self.last_alert_time = {} # Dedup alerts: { "chatid_alerttype_deviceid": timestamp }
        self.message_loop_thread = None

        # Set UI descriptions on startup
        set_bot_descriptions(self.token, enable=bool(self.settings["telegram"].get("SET_DESCRIPTIONS_ON_START", True)))
        print(f"[INIT] {self.service_id} initialized.")

    def _parse_endpoints(self):
        """Extracts topics from serviceInfo."""
        for ep in self.service_info.get("endpoints", []):
            if "MQTT Subscribe:" in ep:
                topic = ep.replace("MQTT Subscribe: ", "").strip()
                self.subscribe_topics.append(topic)
            if "MQTT Publish:" in ep:
                topic = ep.replace("MQTT Publish: ", "").strip()
                if "config_update" in topic:
                    self.config_template = topic
                    print(f"[INIT] Config template found: {self.config_template}")

    # --- MQTT Infrastructure ---

    def setup_mqtt(self):
        try:
            self.mqtt_client.start()
            time.sleep(2)
            self.connected_mqtt = True
            
            for t in self.subscribe_topics:
                self.mqtt_client.mySubscribe(t)
                print(f"[MQTT] Subscribed: {t}")
            return True
        except Exception as e:
            print(f"[MQTT] Connection Error: {e}")
            return False

    def notify(self, topic, payload_bytes):
        """
        Main MQTT Callback.
        Routes messages either to Handler (Config Responses) or Alert Logic.
        """
        print(f"[MQTT] Received: {topic}")
        try:
            payload = json.loads(payload_bytes.decode('utf-8'))
            
            # 1. Check if it's a Config Response (Data/Ack/Error)
            # We look for keywords in the topic or payload structure
            if "config_data" in topic or "config_ack" in topic or "config_error" in topic:
                topic_type = "config_data" if "config_data" in topic else ("config_ack" if "config_ack" in topic else "config_error")
                device_id = payload.get("device_id")
                # Delegate to handlers logic to find the waiting user
                self.handlers.handle_config_response(device_id, payload, topic_type)
                return

            # 2. Otherwise, treat as Alert/Notification
            self._handle_alert_notification(payload, topic)
            
        except json.JSONDecodeError:
            print(f"[MQTT] Non-JSON payload received on {topic}")
        except Exception as e:
            print(f"[ERROR] Notify error: {e}")
            import traceback; traceback.print_exc()

    def _handle_alert_notification(self, payload, topic):
        """Processes Alerts: finds user, checks cooldown, sends Telegram msg."""
        device_id = payload.get('device_id') or payload.get('bn')
        user_id = payload.get('userID')
        msg_text = payload.get('message', 'Event occurred.')
        
        response = requests.get(f"http://catalog:8001/devices/{device_id}") # request to get the device nick
        device_nick = None
        if response.status_code == 200:
            device = response.json()
            device_nick = device.get("user_device_name")

        # Determine Alert Type (from payload or topic)
        alert_type = payload.get('alert_type')
        if not alert_type:
            alert_type = topic.split('/')[-1]
        
        severity = payload.get('severity', 'info')

        # 1. Find Target Chat ID
        target_chat_id = None
        
        if user_id:
            # User-based alert
            u = self.catalog.get(f"/users/{user_id}")
            if u: target_chat_id = u.get('telegram_chat_id')
        
        elif device_id:
            # Device-based alert (find owner)
            d = self.catalog.get(f"/devices/{device_id}")
            if d and d.get('owner'):
                owner_id = d['owner']
                u = self.catalog.get(f"/users/{owner_id}")
                if u: target_chat_id = u.get('telegram_chat_id')
        
        if not target_chat_id:
            print(f"[ALERT] Could not find target chat for alert. (Dev: {device_id}, User: {user_id})")
            return

        # 2. Cooldown Logic
        now = time.time()
        alert_key = f"{target_chat_id}_{alert_type}_{device_id}"
        last_time = self.last_alert_time.get(alert_key, 0)
        cooldown_sec = self.settings.get("defaults", {}).get("alert_cooldown_minutes", 15) * 60
        
        is_door_closed_event = (str(alert_type).lower() == 'doorclosed') or ('door_closed' in str(alert_type).lower())

        # Skip if cooldown active (unless it's a "Resolution" event like Door Closed)
        if not is_door_closed_event and (now - last_time < cooldown_sec):
            print(f"[ALERT] Cooldown active for {alert_key}. Skipping.")
            return

        # 3. Send Message
        try:
            # Visual formatting
            if is_door_closed_event:
                icon = "🚪"
                title = "Door Closed"
                duration = payload.get('duration_seconds')
                dur_text = f" after {duration:.0f}s" if duration else ""
                body = f"\nThe fridge door was closed{dur_text}."
                severity_icon = "" # No severity icon for info
            else:
                icon = "🚨" if severity == "critical" else ("⚠️" if severity == "warning" else "ℹ️")
                title = f"{str(alert_type).replace('_', ' ').title()} Alert"
                body = f"*Details:* {msg_text}"
                if payload.get('recommended_action'):
                    body += f"\n*Suggestion:* {payload['recommended_action']}"
            
            full_msg = f"{icon}* - {title}*\n\n"
            if device_id: full_msg += f"*Device:* {device_nick}\n`(ID: {device_id})`\n"
            full_msg += body

            self.bot.sendMessage(int(target_chat_id), full_msg, parse_mode="Markdown")
            print(f"[ALERT] Sent '{alert_type}' to {target_chat_id}")

            # Update cooldown timestamp
            if not is_door_closed_event:
                self.last_alert_time[alert_key] = now

        except Exception as e:
            print(f"[ALERT] Failed to send Telegram message: {e}")

    # --- Telegram Polling & Routing ---

    def _route_message(self, msg):
        """Routes text messages to Commands or State Handlers."""
        chat_id = msg['chat']['id']
        text = msg.get('text', '').strip()
        
        # 1. Check if user is in a specific STATE (waiting for input)
        status = self.handlers.get_status(chat_id)
        if status:
            state_name = status['state']
            handler = self.handlers.state_handlers.get(state_name)
            
            # If user types a command while in a state, prioritize command (except cancel)
            if text.startswith('/') and not text.startswith('/cancel'):
                pass # Let it fall through to command check
            elif handler:
                print(f"[ROUTER] Routing to state handler: {state_name}")
                handler(chat_id, msg, status['data'])
                return
            else:
                print(f"[ROUTER] No handler found for state {state_name}")
                self.handlers.clear_status(chat_id)

        # 2. Check Commands
        if text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower()
            if cmd in self.handlers.commands:
                print(f"[ROUTER] Routing to command: {cmd}")
                self.handlers.commands[cmd](chat_id, msg)
            else:
                self.bot.sendMessage(chat_id, "Unknown command. Try /help.")
        else:
            if not status:
                self.bot.sendMessage(chat_id, "I don't understand. Use /help to see commands.")

    def _route_callback(self, msg_query):
        """Routes callback queries (button clicks)."""
        query_id, chat_id, data = telepot.glance(msg_query, flavor='callback_query')
        
        # Data format is usually "callback_key arg1 arg2"
        parts = data.split()
        key = parts[0]
        args = parts[1:]
        
        if key in self.handlers.callbacks:
            print(f"[ROUTER] Routing callback: {key} args={args}")
            # Pass everything the handler needs
            self.handlers.callbacks[key](query_id, chat_id, msg_query, *args)
        else:
            print(f"[ROUTER] Unknown callback: {key}")
            self.bot.answerCallbackQuery(query_id, text="Unknown action.")

    def start_telegram_loop(self):
        """Starts the custom polling loop."""
        print("[INIT] Starting Telegram polling loop...")
        
        def loop():
            offset = None
            while self.running:
                try:
                    # Get updates with long polling
                    updates = self.bot.getUpdates(offset=offset, timeout=20)
                    for update in updates:
                        offset = update['update_id'] + 1
                        
                        # 1. Handle Status Updates (Block/Unblock/Group)
                        if 'my_chat_member' in update:
                            self.handlers.handle_my_chat_member(update['my_chat_member'])
                            continue
                            
                        # 2. Handle Messages
                        if 'message' in update:
                            self._route_message(update['message'])
                            
                        # 3. Handle Callbacks
                        elif 'callback_query' in update:
                            self._route_callback(update['callback_query'])
                            
                except Exception as e:
                    if self.running:
                        print(f"[POLLING] Error: {e}")
                        time.sleep(3) # Wait before retry
        
        self.message_loop_thread = threading.Thread(target=loop, daemon=True)
        self.message_loop_thread.start()
        return True

    # --- Lifecycle ---

    def periodic_registration(self):
        """Background thread for keeping service alive in Catalog."""
        interval = self.settings.get("catalog", {}).get("registration_interval_seconds", 300)
        while self.running:
            time.sleep(interval)
            if self.running:
                self.catalog.register_service(self.service_info)

    def run(self):
        print("=" * 60)
        print(f"    {self.service_info['serviceName']} v{self.service_info['version']}")
        print("=" * 60)
        
        if not self.setup_mqtt():
            print("[FATAL] MQTT Setup Failed.")
            return

        # Initial Registration
        if not self.catalog.register_service(self.service_info):
            print("[WARN] Initial Catalog registration failed (will retry in background)")

        # Start Background Threads
        reg_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        reg_thread.start()
        
        self.start_telegram_loop()
        
        print("[INFO] Bot is running. Press CTRL+C to stop.")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Interrupt received.")
            self.stop()

    def stop(self):
        print("[SHUTDOWN] Stopping service...")
        self.running = False
        if self.connected_mqtt:
            self.mqtt_client.stop()
        print("[SHUTDOWN] Bye.")