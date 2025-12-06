import traceback
import time
from influx_service import InfluxDBAdaptor

def main():
    """Main entry point for the InfluxDB Adaptor Service"""
    service = InfluxDBAdaptor()
    
    try:
        service.run()
        
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        traceback.print_exc()
        
    finally:
        if service:
            service.shutdown()

if __name__ == "__main__":
    main()