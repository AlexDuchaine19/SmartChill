import traceback
from optimizer_service import EnergyOptimizationService

def main():
    """Main entry point for the Energy Optimization Service"""
    service = EnergyOptimizationService()
    
    try:
        service.run()
        
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Service stopped by user")
        
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        traceback.print_exc()
        
    finally:
        if service:
            service.shutdown()

if __name__ == "__main__":
    main()