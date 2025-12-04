def handle_config_update(self, topic, payload):
    """Handle configuration update/get via MQTT"""
    try:
        # Parse payload
        if isinstance(payload, bytes):
            payload = payload.decode('utf-8')
        data = json.loads(payload) if isinstance(payload, str) else payload
        
        # Extract device_id from topic: Group17/SmartChill/FoodSpoilageControl/{device_id}/config_update
        topic_parts = topic.split('/')
        if len(topic_parts) >= 5:
            device_id = topic_parts[3]
        else:
            print(f"[CONFIG] Invalid topic format: {topic}")
            return
        
        config_data = data.get('config', {})
        
        # Check if this is a get_config request
        if config_data.get('request') == 'get_config':
            print(f"[CONFIG] Received get_config request for {device_id}")
            current_config = self.get_device_config(device_id)
            
            # Send config_data response
            response_topic = f"Group17/SmartChill/FoodSpoilageControl/{device_id}/config_data"
            response_payload = {
                "device_id": device_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "gas_threshold_ppm": current_config["gas_threshold_ppm"],
                    "enable_continuous_alerts": current_config["enable_continuous_alerts"],
                    "alert_cooldown_minutes": current_config["alert_cooldown_minutes"]
                }
            }
            self.mqtt_client.publish(response_topic, response_payload)
            print(f"[CONFIG] Sent config_data for {device_id}")
            
        else:
            # This is a config update
            print(f"[CONFIG] Received config update for {device_id}: {config_data}")
            
            # Validate and update config
            valid_keys = ["gas_threshold_ppm", "enable_continuous_alerts", "alert_cooldown_minutes"]
            updates = {k: v for k, v in config_data.items() if k in valid_keys}
            
            if updates:
                self.update_device_config(device_id, updates)
                
                # Send acknowledgment
                ack_topic = f"Group17/SmartChill/FoodSpoilageControl/{device_id}/config_ack"
                ack_payload = {
                    "device_id": device_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "updated_config": updates
                }
                self.mqtt_client.publish(ack_topic, ack_payload)
                print(f"[CONFIG] Config updated and acknowledged for {device_id}")
            else:
                # Send error
                error_topic = f"Group17/SmartChill/FoodSpoilageControl/{device_id}/config_error"
                error_payload = {
                    "device_id": device_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": "No valid configuration keys provided"
                }
                self.mqtt_client.publish(error_topic, error_payload)
                print(f"[CONFIG] Invalid config update for {device_id}")
                
    except Exception as e:
        print(f"[CONFIG] Error handling config update: {e}")
