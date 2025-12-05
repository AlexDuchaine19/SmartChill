import traceback
from optimizer_service import EnergyOptimizationService

def main():
    """Main entry point for the Energy Optimization Service"""
    # Inizializza il servizio
    service = EnergyOptimizationService()
    
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
        # Assicura che le risorse (thread, server web) vengano rilasciate correttamente
        if service:
            service.shutdown()

if __name__ == "__main__":
    main()