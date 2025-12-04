import time
import telepot
from telepot.loop import MessageLoop
from modules.utils import load_settings
from modules.catalog_client import CatalogClient, CatalogError
from modules.mqtt_client import MQTTClient
from modules.bot_handlers import BotRequestHandler

class TelegramBotService:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        
        # 1. Load Settings
        try:
            self.settings = load_settings(settings_file)
        except Exception as e:
            print(f"[FATAL] Failed to load settings: {e}")
            raise

        # 2. Initialize Catalog Client
        self.catalog_client = CatalogClient(self.settings)
        
        # 3. Initialize Telegram Bot
        self.token = self.settings["telegram"]["TOKEN"]
        self.bot = telepot.Bot(self.token)
        
        # 4. Initialize MQTT Client
        # Passiamo 'self.bot' e 'self.catalog_client' perché la logica delle notifiche
        # (handle_alert) nel tuo file originale ne ha bisogno per cercare utenti e inviare msg.
        self.mqtt_client = MQTTClient(self.settings, self.bot, self.catalog_client)
        
        # 5. Initialize Bot Handlers
        # Passiamo mqtt_client ai handler perché servono per pubblicare i comandi di config
        self.handler = BotRequestHandler(self.bot, self.catalog_client, self.mqtt_client)
        
        # 6. Link back: Il client MQTT deve poter chiamare il bot handler
        # quando riceve risposte di configurazione (config_data/ack/error)
        self.mqtt_client.bot_handler = self.handler
        
        self.running = True
        self.service_info = self.settings["serviceInfo"]
        print(f"[INIT] {self.service_info['serviceName']} initialized.")

    def start(self):
        print("--- Starting SmartChill Telegram Bot ---")

        # A. Register Service
        if not self.catalog_client.register_service():
            print("[WARN] Initial Catalog registration failed. Will retry.")

        # B. Start MQTT
        if not self.mqtt_client.start():
            print("[ERROR] Failed to start MQTT client.")
            return

        # C. Start Telegram Loop
        print("[INIT] Starting Telegram polling loop...")
        try:
            # Usiamo il metodo 'on_chat_message' e 'on_callback_query' del handler
            self.message_loop = MessageLoop(self.bot, {
                'chat': self.handler.on_chat_message,
                'callback_query': self.handler.on_callback_query
            })
            self.message_loop.run_as_thread()
            print("[INIT] Bot is running. Listening for messages...")
        except Exception as e:
            print(f"[FATAL] Failed to start Telegram loop: {e}")
            self.mqtt_client.stop()
            return

        # D. Main Keep-Alive Loop
        try:
            while self.running:
                time.sleep(10)
                # Periodic re-registration
                self.catalog_client.register_service()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        print("\n[SHUTDOWN] Stopping TelegramBot...")
        self.running = False
        if self.mqtt_client:
            self.mqtt_client.stop()
        print("[SHUTDOWN] TelegramBot stopped.")

if __name__ == "__main__":
    try:
        service = TelegramBotService()
        service.start()
    except Exception as e:
        print(f"[CRITICAL] Service crashed: {e}")