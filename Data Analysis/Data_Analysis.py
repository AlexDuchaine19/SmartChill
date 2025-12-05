import traceback
from data_analysis_service import DataAnalysisService

def main():
    """Main entry point for the Data Analysis Service"""
    # Inizializza il servizio
    service = DataAnalysisService()
    
    try:
        # Avvia il servizio (bloccante finché non viene interrotto)
        service.run()
        
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        traceback.print_exc()
        
    finally:
        # Assicura che le risorse (thread, cherrypy) vengano rilasciate
        service.shutdown()

if __name__ == "__main__":
    main()