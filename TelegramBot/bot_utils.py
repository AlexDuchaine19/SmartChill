import re
import json

def normalize_mac(s: str) -> str:
    """Rimuove separatori e rende maiuscolo il MAC address."""
    return re.sub(r"[^0-9A-Fa-f]", "", (s or "")).upper()

def is_valid_mac(s: str) -> bool:
    """Verifica se il MAC è valido (12 caratteri esadecimali)."""
    return len(normalize_mac(s)) == 12

def is_valid_username(s: str) -> bool:
    """Verifica formato username (3-32 caratteri alfanumerici)."""
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", s or ""))

def escape_markdown(text):
    """Esegue l'escape dei caratteri speciali per Markdown V2 di Telegram."""
    if text is None:
        return "N/A"
    # Caratteri che richiedono escape in Markdown V2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    text = str(text)
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    return text

def load_settings(filename):
    """Carica e valida il file settings.json."""
    try:
        with open(filename, 'r') as f:
            settings_data = json.load(f)
            
            # Validazione di base per evitare crash all'avvio
            if "telegram" not in settings_data or "TOKEN" not in settings_data["telegram"]:
                raise ValueError("Missing 'telegram' or 'TOKEN' in settings.")
            if "catalog" not in settings_data or "url" not in settings_data["catalog"]:
                raise ValueError("Missing 'catalog' or 'url' in settings.")
            if "mqtt" not in settings_data or "brokerIP" not in settings_data["mqtt"] or "brokerPort" not in settings_data["mqtt"]:
                raise ValueError("Missing 'mqtt' config (brokerIP, brokerPort) in settings.")
                
            return settings_data
    except FileNotFoundError:
        print(f"[ERROR] Settings file '{filename}' not found.")
        raise
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ERROR] Invalid or incomplete settings file '{filename}': {e}")
        raise

def get_setting_details(field_name):
    """
    Restituisce i dettagli completi per la UI di ogni impostazione.
    Include Nome, Descrizione e Range visuale per mantenere l'output originale.
    """
    settings_map = {
        # --- TimerUsageControl Settings ---
        "max_door_open_seconds": {
            "name": "Door Open Timeout",
            "desc": "Maximum duration the door can remain open before triggering an alert.",
            "range_text": "(30-300 seconds)",
            "min": 30, "max": 300, "type": int
        },
        "check_interval": {
            "name": "Check Interval",
            "desc": "Frequency of monitoring checks for door violations.",
            "range_text": "(1-30 seconds)",
            "min": 1, "max": 30, "type": int
        },
        "enable_door_closed_alerts": {
            "name": "Door Closed Alerts",
            "desc": "Send notification when door closes after exceeding timeout.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Enabled",
            "false_text": "Disabled"
        },
        
        # --- FoodSpoilageControl Settings ---
        "gas_threshold_ppm": {
            "name": "Gas Level Threshold",
            "desc": "Gas concentration level that triggers spoilage alerts.",
            "range_text": "(100-1000 PPM)",
            "min": 100, "max": 1000, "type": int
        },
        "alert_cooldown_minutes": {
            "name": "Alert Cooldown Period",
            "desc": "Minimum time between consecutive alerts to prevent spam.",
            "range_text": "(5-120 minutes)",
            "min": 5, "max": 120, "type": int
        },
        "enable_continuous_alerts": {
            "name": "Alert Frequency",
            "desc": "Configure how and when spoilage alerts are triggered.",
            "range_text": "(On Breach Only / Continuous)",
            "type": bool,
            "true_text": "Continuous while above threshold",
            "false_text": "On Breach Only"
        },
        
        # --- FridgeStatusControl Settings ---
        "temp_min_celsius": {
            "name": "Minimum Temperature",
            "desc": "Acceptable temperature range lower bound.",
            "range_text": "(-5 to 5 °C)",
            "min": -5, "max": 5, "type": float
        },
        "temp_max_celsius": {
            "name": "Maximum Temperature",
            "desc": "Acceptable temperature range upper bound.",
            "range_text": "(5 to 15 °C)",
            "min": 5, "max": 15, "type": float
        },
        "humidity_max_percent": {
            "name": "Humidity Threshold",
            "desc": "Maximum humidity level before triggering malfunction alerts.",
            "range_text": "(50-95 %)",
            "min": 50, "max": 95, "type": float
        },
        "enable_malfunction_alerts": {
            "name": "Malfunction Alerts",
            "desc": "Control when malfunction alerts are sent.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Enabled", 
            "false_text": "Disabled"
        }
    }
    
    # Restituisce il dizionario trovato o un placeholder generico se la chiave non esiste
    return settings_map.get(field_name, {
        "name": field_name, 
        "desc": "", 
        "range_text": "", 
        "type": "unknown"
    })