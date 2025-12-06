import traceback
from data_analysis_service import DataAnalysisService

def main():
    """Main entry point for the Data Analysis Service"""
    service = DataAnalysisService()
    
    try:
        service.run()
        
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        traceback.print_exc()
        
    finally:
        service.shutdown()

if __name__ == "__main__":
    main()