import time
import telepot
from telepot.loop import MessageLoop
from modules.utils import load_settings
from modules.catalog_client import CatalogClient
from modules.mqtt_client import MQTTClient
from modules.bot_handlers import BotRequestHandler

class TelegramBot:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = load_settings(settings_file)
        
        # Initialize components
        self.catalog_client = CatalogClient(self.settings)
        
        # Initialize Bot
        self.token = self.settings["telegram"]["TOKEN"]
        self.bot = telepot.Bot(self.token)
        
        # Initialize MQTT
        self.mqtt_client = MQTTClient(self.settings, self.bot, self.catalog_client)
        
        # Initialize Handlers
        self.handler = BotRequestHandler(self.bot, self.catalog_client, self.mqtt_client)
        
        print("[INIT] TelegramBot initialized.")

    def start(self):
        # Register with Catalog
        if not self.catalog_client.register_service():
            print("[WARN] Initial Catalog registration failed. Will retry periodically.")

        # Start MQTT
        if not self.mqtt_client.start():
            print("[ERROR] Failed to start MQTT client.")
            return

        # Start Bot Loop
        MessageLoop(self.bot, {
            'chat': self.handler.on_chat_message,
            'callback_query': self.handler.on_callback_query
        }).run_as_thread()
        print("[INFO] Bot is running. Listening for messages...")

        # Main Loop
        try:
            while True:
                time.sleep(10)
                self.catalog_client.check_registration()
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Stopping TelegramBot...")
            self.mqtt_client.stop()
            print("[SHUTDOWN] TelegramBot stopped.")

if __name__ == "__main__":
    bot = TelegramBot()
    bot.start()