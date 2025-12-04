import time
import json
from datetime import datetime, timezone
from MyMQTT import MyMQTT
from modules.catalog_client import CatalogError

class MQTTClient:
    def __init__(self, settings, bot, catalog_client):
        self.settings = settings
        self.bot = bot
        self.catalog_client = catalog_client
        
        self.broker_host = settings["mqtt"]["brokerIP"]
        self.broker_port = settings["mqtt"]["brokerPort"]
        self.client_id = f"TelegramBot_{int(time.time())}"
        
        self.mqtt_client = MyMQTT(self.client_id, self.broker_host, self.broker_port, self)
        
        self.connected = False
        self.last_alert_time = {} 
        self.bot_handler = None 

    def start(self):
        try:
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            self.subscribe_to_topics()
            return True
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

    def stop(self):
        if self.mqtt_client:
            self.mqtt_client.stop()
            self.connected = False

    def subscribe_to_topics(self):
        # 1. Alert topics
        endpoints = self.settings["serviceInfo"].get("endpoints", [])
        for ep in endpoints:
            if "MQTT Subscribe:" in ep:
                topic = ep.replace("MQTT Subscribe:", "").strip()
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed: {topic}")
        
        # 2. Config topics (WILDCARD CRITICHE)
        # Assicuriamoci di prendere tutte le varianti possibili
        print("[MQTT] Subscribing to Config Topics...")
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/+/config_data")
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/+/config_ack")
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/+/config_error")
        # Fallback per topic più corti
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/config_data") 

    def publish_service_config_update(self, service_name, device_id, config_data):
        topic = f"Group17/SmartChill/{service_name}/{device_id}/config_update"
        payload = {
            "type": "device_config_update",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": config_data,
            "device_id": device_id
        }
        print(f"--- DEBUG MQTT PUBLISH ---")
        print(f"Topic: {topic}")
        print(f"Payload: {json.dumps(payload)}")
        self.mqtt_client.myPublish(topic, payload)
        print("--------------------------")

    def notify(self, topic, payload_bytes):
        # print(f"[MQTT] Msg on: {topic}") 
        try:
            payload_str = payload_bytes.decode('utf-8')
            payload = json.loads(payload_str)

            # --- DEBUG CONFIG ---
            if "config_" in topic:
                print(f"\n>>> DEBUG MQTT RECEIVED CONFIG <<<")
                print(f"Topic: {topic}")
                print(f"Payload keys: {list(payload.keys())}")
                
                if self.bot_handler:
                    print("Delegating to bot_handler.process_config_response...")
                    self.bot_handler.process_config_response(topic, payload)
                else:
                    print("ERROR: self.bot_handler is None! Cannot process config.")
                print(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\n")
                return
            # --------------------

            # Handle Alerts
            if "/Alerts/" in topic:
                self.handle_alert(topic, payload)

        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")

    def handle_alert(self, topic, payload):
        """Process alert messages and forward to Telegram"""
        device_id = payload.get('device_id') or payload.get('bn')
        user_id = payload.get('userID')
        alert_message = payload.get('message', 'An event occurred.')
        
        # Determine Alert Type
        alert_type = payload.get('alert_type')
        if not alert_type:
            alert_type = topic.split('/')[-1] # Fallback to topic suffix
        
        severity = payload.get('severity', 'info')
        target_chat_id = None

        # 1. Find User to Notify (using Catalog Client)
        if user_id:
            try:
                user_info = self.catalog_client.get_user(user_id)
                target_chat_id = user_info.get('telegram_chat_id')
            except: pass
        elif device_id:
            try:
                device_info = self.catalog_client.get_device(device_id)
                # Check if assigned
                if device_info.get('user_assigned') and device_info.get('assigned_user'):
                    owner_id = device_info['assigned_user']
                    user_info = self.catalog_client.get_user(owner_id)
                    target_chat_id = user_info.get('telegram_chat_id')
            except: pass

        if target_chat_id:
            # 2. Cooldown Logic
            now = time.time()
            alert_key = f"{target_chat_id}_{alert_type}_{device_id}"
            last_time = self.last_alert_time.get(alert_key, 0)
            cooldown_min = self.settings.get("defaults", {}).get("alert_cooldown_minutes", 15)
            
            is_info_alert = alert_type.lower() == 'doorclosed' # No cooldown for 'DoorClosed'

            if not is_info_alert and (now - last_time < cooldown_min * 60):
                print(f"[NOTIFY] Cooldown active for {alert_type} -> {target_chat_id}. Skipping.")
                return

            # 3. Format & Send Message
            icon = "🚨" if severity == "critical" else ("⚠️" if severity == "warning" else "ℹ️")
            if is_info_alert: icon = "🚪"
            
            msg = f"{icon} **{alert_type.replace('_', ' ').upper()}**\n\n"
            if device_id: msg += f"**Device:** `{device_id}`\n"
            msg += f"**Msg:** {alert_message}\n"
            if payload.get('recommended_action'):
                msg += f"**Tip:** {payload['recommended_action']}\n"
            msg += f"\n_{datetime.now().strftime('%H:%M:%S')}_"

            try:
                self.bot.sendMessage(int(target_chat_id), msg, parse_mode="Markdown")
                print(f"[NOTIFY] Sent {alert_type} to {target_chat_id}")
                if not is_info_alert:
                    self.last_alert_time[alert_key] = now
            except Exception as e:
                print(f"[NOTIFY] Telegram send error: {e}")