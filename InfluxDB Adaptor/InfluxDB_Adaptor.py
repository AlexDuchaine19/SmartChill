import traceback
import time
from influx_service import InfluxDBAdaptor

def main():
    """Main entry point for the InfluxDB Adaptor Service"""
    # Inizializza il servizio
    service = InfluxDBAdaptor()
    
    try:
        # Avvia il servizio (metodo bloccante)
        service.run()
        
    except Exception as e:
        # Gestione errori critici che causano il crash del servizio
        print(f"[FATAL] Service error: {e}")
        traceback.print_exc()
        
    finally:
        # Assicura che le connessioni (Influx, MQTT, CherryPy) vengano chiuse correttamente
        # anche in caso di crash
        if service:
            service.shutdown()

if __name__ == "__main__":
    main()