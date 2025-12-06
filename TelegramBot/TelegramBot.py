import traceback
import sys
from bot_service import TelegramBotService

SETTINGS_FILE = "settings.json"

def main():
    """
    Main entry point for the SmartChill Telegram Bot.
    """
    bot_service = None
    
    try:
        bot_service = TelegramBotService(SETTINGS_FILE)
        
        bot_service.run()

    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"[FATAL] Initialization failed: {e}")
        print("[HINT] Please check your 'settings.json' file structure.")
        sys.exit(1)

    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Interrupted by user.")

    except Exception as e:
        print(f"[FATAL] An unexpected error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)

    finally:
        if bot_service:
            try:
                bot_service.stop()
            except Exception as stop_error:
                print(f"[SHUTDOWN] Error during forced stop: {stop_error}")

if __name__ == "__main__":
    main()