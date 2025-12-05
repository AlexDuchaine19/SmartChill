import traceback
from spoilage_service import FoodSpoilageControl

def main():
    """Main entry point for the Food Spoilage Control Service"""
    # Inizializza il servizio
    service = FoodSpoilageControl()
    
    try:
        # Avvia il servizio (metodo bloccante)
        service.run()
        
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Service stopped by user")
        
    except Exception as e:
        # Gestione errori critici
        print(f"[FATAL] Service error: {e}")
        traceback.print_exc()
        
    finally:
        # Assicura che le risorse (thread, connessioni MQTT) vengano rilasciate
        if service:
            service.shutdown()

if __name__ == "__main__":
    main()