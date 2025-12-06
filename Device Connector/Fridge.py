import traceback
from fridge_service import FridgeSimulator

def main():
    """Main entry point for the SmartChill Fridge Simulator"""
    try:
        simulator = FridgeSimulator()
        
        simulator.run()
        
    except FileNotFoundError as e:
        print(f"[FATAL] Configuration error: {e}")
        print("[FATAL] Please ensure settings.json exists with proper configuration")
        
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Simulator stopped by user")
        
    except Exception as e:
        print(f"[FATAL] Simulator error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()