import traceback
from fridge_service import FridgeSimulator

def main():
    """Main entry point for the SmartChill Fridge Simulator"""
    try:
        # Istanzia il simulatore
        simulator = FridgeSimulator()
        
        # Avvia il ciclo principale di simulazione
        simulator.run()
        
    except FileNotFoundError as e:
        # Gestione specifica per file di configurazione mancanti
        print(f"[FATAL] Configuration error: {e}")
        print("[FATAL] Please ensure settings.json exists with proper configuration")
        
    except KeyboardInterrupt:
        # Gestione interruzione manuale (Ctrl+C) se non catturata dentro run()
        print("\n[SHUTDOWN] Simulator stopped by user")
        
    except Exception as e:
        # Gestione errori generici non previsti
        print(f"[FATAL] Simulator error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()