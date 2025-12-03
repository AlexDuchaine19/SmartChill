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
        self.client_id = f"SmartChill_TelegramBot_{int(time.time())}"
        self.mqtt_client = None
        self.connected = False
        self.last_alert_time = {}  # {alert_key: timestamp}

    def start(self):
        """Start MQTT client"""
        try:
            self.mqtt_client = MyMQTT(self.client_id, self.broker_host, self.broker_port, self)
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            
            # Subscribe to topics
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
        """Subscribe to relevant topics"""
        # Subscribe to all alerts
        self.mqtt_client.mySubscribe("Group17/SmartChill/Alerts/#")
        # Subscribe to responses for config updates
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/config_ack")
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/config_error")
        # Subscribe to config data responses
        self.mqtt_client.mySubscribe("Group17/SmartChill/+/config_data")
        print("[MQTT] Subscribed to alert and config topics")

    def publish_config_update(self, device_id, config_type, config_data):
        """Publish configuration update to a device/service"""
        topic = f"Group17/SmartChill/Devices/{device_id}/config_update"
        # If it's a service, topic might be different, but assuming device for now
        # Actually, for services it might be Group17/SmartChill/{ServiceID}/config_update
        # Let's assume device_id is sufficient or we handle service IDs too
        
        payload = {
            "type": config_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": config_data
        }
        self.mqtt_client.myPublish(topic, payload)
        print(f"[MQTT] Published config update to {topic}")

    def notify(self, topic, payload_bytes):
        """Handle incoming MQTT messages (Alerts & Config Responses)"""
        print(f"[MQTT] Received message on topic: {topic}")
        try:
            payload = json.loads(payload_bytes.decode('utf-8'))

            # Handle config responses
            if "config_data" in topic or "config_ack" in topic or "config_error" in topic:
                # This might need to be handled by a callback or event system
                # For now, just print or store? 
                # The original code called self.handle_config_response(topic, payload)
                # We can implement a simple handler here or delegate
                self.handle_config_response(topic, payload)
                return

            # Handle Alerts
            device_id = payload.get('device_id') or payload.get('bn') 
            user_id = payload.get('userID') 
            alert_message = payload.get('message', 'An event occurred.')
            alert_type_from_payload = payload.get('alert_type')
            alert_type_from_topic = topic.split('/')[-1]
            alert_type = alert_type_from_payload or alert_type_from_topic
            severity = payload.get('severity', 'info')

            target_chat_id = None

            # Determine target chat ID
            if user_id:
                try:
                    user_info = self.catalog_client.get_user(user_id)
                    target_chat_id = user_info.get('telegram_chat_id')
                except CatalogError as e:
                    print(f"[ALERT] Failed to get user info for {user_id}: {e}")
            elif device_id:
                try:
                    device_info = self.catalog_client.get_device(device_id)
                    if device_info.get('user_assigned') and device_info.get('owner'):
                        assigned_user_id = device_info['owner']
                        user_info = self.catalog_client.get_user(assigned_user_id)
                        target_chat_id = user_info.get('telegram_chat_id')
                except CatalogError as e:
                    print(f"[ALERT] Failed to get device/user info for {device_id}: {e}")

            if target_chat_id:
                # Cooldown check
                now = time.time()
                alert_key_base = f"{target_chat_id}_{alert_type}"
                alert_key = f"{alert_key_base}_{device_id}" if device_id else alert_key_base
                
                last_time = self.last_alert_time.get(alert_key, 0)
                cooldown_sec = self.settings.get("defaults", {}).get("alert_cooldown_minutes", 15) * 60
                is_info_alert = alert_type.lower() == 'doorclosed'

                # if not is_info_alert and (now - last_time < cooldown_sec):
                #     return

                # Format Message
                if alert_type.lower() == 'doorclosed':
                    icon = "🚪"
                    title = "Door Closed"
                    duration = payload.get('duration_seconds')
                    duration_text = f" after being open for {duration:.0f} seconds" if duration is not None else ""
                    telegram_msg = f"{icon} **{title}** {icon}\n\n"
                    if device_id: telegram_msg += f"**Device:** `{device_id}`\n"
                    telegram_msg += f"The fridge door was closed{duration_text}.\n"
                    telegram_msg += f"\n_Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"
                    severity = 'info'
                else:
                    icon = "🚨" if severity == "critical" else ("⚠️" if severity == "warning" else "ℹ️")
                    title = f"Alert: {alert_type}"
                    telegram_msg = f"{icon} **{title}** {icon}\n\n"
                    if device_id: telegram_msg += f"**Device:** `{device_id}`\n"
                    telegram_msg += f"**Details:** {alert_message}\n"
                    if payload.get('recommended_action'): telegram_msg += f"**Suggestion:** {payload['recommended_action']}\n"
                    telegram_msg += f"\n_Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"

                self.bot.sendMessage(int(target_chat_id), telegram_msg, parse_mode="Markdown")
                print(f"[NOTIFY] Alert '{alert_type}' sent to user {target_chat_id}")
                
                self.last_alert_time[alert_key] = now
            else:
                print(f"[ALERT] Could not determine target chat ID for alert: {topic}")

        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")

    def handle_config_response(self, topic, payload):
        """Handle configuration responses"""
        # This is used for the settings menu feedback
        # We might need a way to pass this back to the bot handler
        # For now, just print
        print(f"[CONFIG_RESPONSE] {topic}: {payload}")
        # In a full implementation, we'd update some state or notify the user via bot
