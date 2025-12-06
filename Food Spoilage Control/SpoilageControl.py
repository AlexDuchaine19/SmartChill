import traceback
from spoilage_service import FoodSpoilageControl

def main():
    """Main entry point for the Food Spoilage Control Service"""
    service = FoodSpoilageControl()
    
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