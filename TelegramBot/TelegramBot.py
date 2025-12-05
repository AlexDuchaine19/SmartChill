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
        # Inizializza il servizio (carica settings, catalog, bot, mqtt)
        bot_service = TelegramBotService(SETTINGS_FILE)
        
        # Avvia il loop principale
        bot_service.run()

    except (FileNotFoundError, ValueError, KeyError) as e:
        # Errori specifici di configurazione (file mancante, chiavi assenti)
        print(f"[FATAL] Initialization failed: {e}")
        print("[HINT] Please check your 'settings.json' file structure.")
        sys.exit(1)

    except KeyboardInterrupt:
        # Gestione interruzione manuale (Ctrl+C) se non catturata prima
        print("\n[SHUTDOWN] Interrupted by user.")

    except Exception as e:
        # Cattura qualsiasi altro errore imprevisto per evitare crash silenziosi
        print(f"[FATAL] An unexpected error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)

    finally:
        # Assicura sempre la chiusura delle risorse (socket, thread)
        if bot_service:
            try:
                bot_service.stop()
            except Exception as stop_error:
                print(f"[SHUTDOWN] Error during forced stop: {stop_error}")

if __name__ == "__main__":
    main()