import telepot
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timezone
from bot_utils import (
    is_valid_mac, 
    is_valid_username, 
    normalize_mac, 
    escape_markdown, 
    get_setting_details
)

class BotHandlers:
    def __init__(self, bot, catalog_client, mqtt_client, config_template):
        self.bot = bot
        self.catalog = catalog_client
        self.mqtt = mqtt_client
        self.config_template = config_template
        
        # State Management: {chat_id: {"state": "name", "data": {...}}}
        self.user_states = {} 
        
        # Command Mappings
        self.commands = {
            "/start": self.cmd_start,
            "/help": self.cmd_help,
            "/newdevice": self.cmd_newdevice,
            "/mydevices": self.cmd_mydevices,
            "/settings": self.cmd_settings,
            "/showme": self.cmd_showme,
            "/deleteme": self.cmd_deleteme,
            "/cancel": self.cmd_cancel
        }
        
        # Callback Mappings (Button clicks)
        self.callbacks = {
            "cb_quit_menu": self.cb_quit_menu,
            "cb_device_menu": self.cb_device_menu,
            "cb_device_info": self.cb_device_info,
            "cb_device_unassign": self.cb_device_unassign,
            "cb_device_rename": self.cb_device_rename,
            "cb_settings_menu": self.cb_settings_menu,
            "cb_service_menu": self.cb_service_menu,
            "cb_show_current_info": self.cb_show_current_info,
            "cb_service_modify": self.cb_service_modify,
            "cb_change_value": self.cb_change_value,
            "cb_edit_boolean": self.cb_edit_boolean,
            "cb_set_boolean": self.cb_set_boolean,
            "cb_service_menu_back": self.cb_service_menu_back,
            "cb_newdevice_start": self.cb_newdevice_start
        }

        # State Handlers (User text input)
        self.state_handlers = {
            "waiting_for_mac": self.handle_mac_input,
            "waiting_for_username": self.handle_username_input,
            "waiting_for_newdevice_mac": self.handle_newdevice_mac,
            "waiting_for_device_rename": self.handle_device_rename_input,
            "waiting_for_new_value": self.handle_new_value_input,
            "waiting_for_username_link": self.handle_username_link, # Logica Node-RED
            "waiting_for_config": None # Stato di attesa passiva (aspetta MQTT)
        }

    # --- Helper Methods ---
    def set_status(self, chat_id, state_name, **kwargs):
        self.user_states[chat_id] = {"state": state_name, "data": kwargs}
        print(f"[STATE] {chat_id} -> {state_name}")

    def get_status(self, chat_id):
        return self.user_states.get(chat_id)

    def clear_status(self, chat_id):
        removed = self.user_states.pop(chat_id, None)
        if removed:
            print(f"[STATE] {chat_id} exit {removed['state']}")
        return removed

    def _get_username(self, msg):
        u = msg.get("from", {})
        return u.get("first_name") or u.get("username") or f"User_{u.get('id')}"

    # --- Command Handlers ---

    def cmd_start(self, chat_id, msg, *args):
        username = self._get_username(msg)
        self.bot.sendMessage(chat_id, f"👋 Welcome, {username}!")
        
        user = self.catalog.get_user_by_chat_id(chat_id)
        if user and user.get("devicesList"):
            self.bot.sendMessage(chat_id, "You seem to be already set up.\nUse /mydevices or /help.")
            self.clear_status(chat_id)
            return
            
        self.bot.sendMessage(
            chat_id,
            "To link your SmartChill account, please enter the **MAC address** of your fridge.\n"
            "(Format: `XX:XX:XX:XX:XX:XX` or `AABBCC112233`)",
            parse_mode="Markdown"
        )
        self.set_status(chat_id, "waiting_for_mac")

    def cmd_help(self, chat_id, msg, *args):
        txt = (
            "Commands:\n"
            "/start – Start menu\n"
            "/newdevice – Add a new device\n"
            "/mydevices – List your devices\n"
            "/settings – Configure devices\n"
            "/showme – Show account info\n"
            "/deleteme – Delete account\n"
            "/cancel – Cancel action"
        )
        self.bot.sendMessage(chat_id, txt)

    def cmd_newdevice(self, chat_id, msg, *args):
        user = self.catalog.get_user_by_chat_id(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are not registered yet. Use /start first.")
            return
        self.bot.sendMessage(
            chat_id,
            "Please enter the **MAC address** of the new fridge to link to your account.\n"
            "(Format: `XX:XX:XX:XX:XX:XX` or `AABBCC112233`)",
            parse_mode="Markdown"
        )
        self.set_status(chat_id, "waiting_for_newdevice_mac", user_id=user["userID"])

    def cmd_mydevices(self, chat_id, msg, *args):
        user = self.catalog.get_user_by_chat_id(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are not registered yet. Use /start to begin.")
            return
        try:
            # Recupera la lista aggiornata dal catalog
            devices = self.catalog.get(f"/users/{user['userID']}/devices")
            if not devices:
                self.bot.sendMessage(chat_id, "You have no devices yet. Use /newdevice to add one.")
                return

            buttons = []
            for d in devices:
                name = d.get('user_device_name') or d.get('deviceID') or 'Unknown'
                buttons.append([InlineKeyboardButton(text=f"🧊 {name}", callback_data=f"cb_device_menu {d.get('deviceID')}")])
            
            buttons.append([InlineKeyboardButton(text="➕ Add new device", callback_data="cb_newdevice_start")])

            self.bot.sendMessage(chat_id, "Your Devices:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        except Exception as e:
            self.bot.sendMessage(chat_id, f"⚠️ Failed to retrieve devices: {e}")

    def cmd_settings(self, chat_id, msg, *args):
        # Alias per mydevices, dato che le impostazioni sono dentro il menu del device
        self.cmd_mydevices(chat_id, msg, *args)

    def cmd_showme(self, chat_id, msg, *args):
        user = self.catalog.get_user_by_chat_id(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are not registered. Use /start.")
            return
        
        dev_count = len(user.get('devicesList', []))
        message = (f"👤 **User Info**\n"
                   f"**Catalog UserID:** `{user['userID']}`\n"
                   f"**Telegram Name:** {user['userName']}\n"
                   f"**Telegram ChatID:** `{chat_id}`\n"
                   f"**Registered:** {user.get('registration_time', 'N/A')}\n"
                   f"**Assigned Devices:** {dev_count}")
        self.bot.sendMessage(chat_id, message, parse_mode="Markdown")

    def cmd_deleteme(self, chat_id, msg, *args):
        user = self.catalog.get_user_by_chat_id(chat_id)
        if not user:
            self.bot.sendMessage(chat_id, "You are already not registered.")
            return
        
        try:
            uid = user.get('userID')
            self.catalog.delete(f"/users/{uid}")
            self.bot.sendMessage(chat_id, f"✅ User {user['userName']} deleted. Devices unassigned.")
        except Exception as e:
            self.bot.sendMessage(chat_id, f"❌ Deletion failed: {e}")

    def cmd_cancel(self, chat_id, msg, *args):
        removed = self.clear_status(chat_id)
        if removed:
            self.bot.sendMessage(chat_id, "Operation cancelled.")
        else:
            self.bot.sendMessage(chat_id, "No active operation to cancel.")

    # --- Callback Handlers (Menu Navigation) ---

    def cb_quit_menu(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        self.bot.editMessageText(telepot.message_identifier(msg_query['message']), "Menu closed.")

    def cb_device_menu(self, query_id, chat_id, msg_query, *args):
        did = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        buttons = [
            [InlineKeyboardButton(text="ℹ️ Show Info", callback_data=f"cb_device_info {did}")],
            [InlineKeyboardButton(text="✏️ Rename Device", callback_data=f"cb_device_rename {did}")],
            [InlineKeyboardButton(text="⚙️ Settings", callback_data=f"cb_settings_menu {did}")],
            [InlineKeyboardButton(text="❌ Unassign Device", callback_data=f"cb_device_unassign {did}")],
            [InlineKeyboardButton(text="« Back", callback_data="/mydevices")],
            [InlineKeyboardButton(text="Close Menu", callback_data="cb_quit_menu")]
        ]
        
        self.bot.editMessageText(
            msg_id, 
            f"Options for device `{escape_markdown(did)}`:", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="Markdown"
        )

    def cb_device_info(self, query_id, chat_id, msg_query, *args):
        did = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        try:
            device = self.catalog.get(f"/devices/{did}")
            if not device:
                self.bot.editMessageText(msg_id, "⚠️ Device not found.")
                return

            txt = (
                f"📘 *Device Information*\n\n"
                f"🆔 *ID:* `{escape_markdown(did)}`\n"
                f"🏷️ *Name:* {escape_markdown(device.get('user_device_name', 'N/A'))}\n"
                f"🔢 *MAC:* `{escape_markdown(device.get('mac_address', 'N/A'))}`\n"
                f"👤 *Assigned:* {'Yes' if device.get('user_assigned') else 'No'}\n"
                f"📡 *Status:* {escape_markdown(device.get('status', 'Unknown'))}"
            )
            
            buttons = [[InlineKeyboardButton(text="« Back", callback_data=f"cb_device_menu {did}")]]
            self.bot.editMessageText(msg_id, txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
            
        except Exception as e:
            self.bot.editMessageText(msg_id, f"Error: {e}")

    def cb_device_unassign(self, query_id, chat_id, msg_query, *args):
        did = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        self.bot.editMessageText(msg_id, f"🔄 Unassigning `{escape_markdown(did)}`...", parse_mode="Markdown")
        
        try:
            self.catalog.post(f"/devices/{did}/unassign", None)
            self.bot.editMessageText(msg_id, f"✅ Device `{escape_markdown(did)}` unassigned.", parse_mode="Markdown")
            self.bot.sendMessage(chat_id, "Use /mydevices to refresh.")
        except Exception as e:
            self.bot.editMessageText(msg_id, f"❌ Failed: {e}")

    def cb_device_rename(self, query_id, chat_id, msg_query, *args):
        did = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        # Recupera info per mostrare nome attuale
        try:
            d = self.catalog.get(f"/devices/{did}")
            curr = d.get('user_device_name', 'N/A')
        except: curr = "Unknown"

        self.bot.editMessageText(
            msg_id,
            f"✏️ **Rename Device**\n\nDevice: `{escape_markdown(did)}`\nCurrent: *{escape_markdown(curr)}*\n\nEnter new name:",
            parse_mode="Markdown"
        )
        self.set_status(chat_id, "waiting_for_device_rename", device_id=did, old_name=curr, msg_identifier=msg_id)

    def cb_settings_menu(self, query_id, chat_id, msg_query, *args):
        did = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        buttons = [
            [InlineKeyboardButton(text="⏱️ Door Timer", callback_data=f"cb_service_menu {did} TimerUsageControl")],
            [InlineKeyboardButton(text="🔥 Food Spoilage", callback_data=f"cb_service_menu {did} FoodSpoilageControl")],
            [InlineKeyboardButton(text="🌡️ Fridge Status", callback_data=f"cb_service_menu {did} FridgeStatusControl")],
            [InlineKeyboardButton(text="« Back to Device", callback_data=f"cb_device_menu {did}")]
        ]
        self.bot.editMessageText(
            msg_id, 
            f"⚙️ **Settings**\nSelect a service for `{escape_markdown(did)}`:", 
            parse_mode="Markdown", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    def cb_service_menu(self, query_id, chat_id, msg_query, *args):
        did, svc = args[0], args[1]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        if not self.config_template:
            self.bot.editMessageText(msg_id, "❌ Error: Bot not configured for publishing.")
            return

        # Prepare state to wait for MQTT response
        self.set_status(chat_id, "waiting_for_config", device_id=did, service_name=svc, msg_identifier=msg_id)
        
        # Publish MQTT request
        topic = self.config_template.format(service_name=svc, device_id=did)
        try:
            self.mqtt.myPublish(topic, {"type": "config_get", "device_id": did})
            self.bot.editMessageText(msg_id, f"🔄 Fetching settings for *{escape_markdown(svc)}*...", parse_mode="Markdown")
        except Exception as e:
            self.bot.editMessageText(msg_id, f"❌ MQTT Error: {e}")
            self.clear_status(chat_id)

    def cb_show_current_info(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        
        # Recupera dati dallo stato (che ora contiene la config ricevuta via MQTT)
        state = self.get_status(chat_id)
        if not state or not state.get('data'):
            self.bot.sendMessage(chat_id, "❌ Session expired.")
            return
            
        data = state['data']
        config = data.get("config", {})
        svc = data.get("service_name")
        msg_id = telepot.message_identifier(msg_query['message'])
        
        txt = f"ℹ️ Current *{escape_markdown(svc)}* Settings:\n\n"
        if not config:
            txt += "_No settings found._"
        else:
            for k, v in config.items():
                details = get_setting_details(k)
                name = details.get('name', k)
                txt += f"▪️ *{name}*: `{v}`\n"
        
        buttons = [[InlineKeyboardButton(text="« Back", callback_data="cb_service_menu_back")]]
        self.bot.editMessageText(msg_id, txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_service_modify(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        state = self.get_status(chat_id)
        if not state or not state.get('data'): return
        
        data = state['data']
        config = data.get("config", {})
        svc = data.get("service_name")
        msg_id = telepot.message_identifier(msg_query['message'])
        
        buttons = []
        
        # Logica per generare bottoni specifici per servizio
        # TimerUsageControl
        if svc == "TimerUsageControl":
            val = config.get('max_door_open_seconds', 'N/A')
            buttons.append([InlineKeyboardButton(text=f"Max Door Open: {val}s", callback_data="cb_change_value max_door_open_seconds")])
            val = config.get('check_interval', 'N/A')
            buttons.append([InlineKeyboardButton(text=f"Check Interval: {val}s", callback_data="cb_change_value check_interval")])
            
            field = 'enable_door_closed_alerts'
            det = get_setting_details(field)
            curr = det.get("true_text") if config.get(field) else det.get("false_text")
            buttons.append([InlineKeyboardButton(text=f"{det['name']}: {curr}", callback_data=f"cb_edit_boolean {field}")])

        # FoodSpoilageControl
        elif svc == "FoodSpoilageControl":
            val = config.get('gas_threshold_ppm', 'N/A')
            buttons.append([InlineKeyboardButton(text=f"Gas Threshold: {val} PPM", callback_data="cb_change_value gas_threshold_ppm")])
            val = config.get('alert_cooldown_minutes', 'N/A')
            buttons.append([InlineKeyboardButton(text=f"Alert Cooldown: {val} min", callback_data="cb_change_value alert_cooldown_minutes")])
            
            field = 'enable_continuous_alerts'
            det = get_setting_details(field)
            curr = det.get("true_text") if config.get(field) else det.get("false_text")
            buttons.append([InlineKeyboardButton(text=f"{det['name']}: {curr}", callback_data=f"cb_edit_boolean {field}")])

        # FridgeStatusControl
        elif svc == "FridgeStatusControl":
            buttons.append([InlineKeyboardButton(text=f"Min Temp: {config.get('temp_min_celsius')}°C", callback_data="cb_change_value temp_min_celsius")])
            buttons.append([InlineKeyboardButton(text=f"Max Temp: {config.get('temp_max_celsius')}°C", callback_data="cb_change_value temp_max_celsius")])
            buttons.append([InlineKeyboardButton(text=f"Max Humidity: {config.get('humidity_max_percent')}%", callback_data="cb_change_value humidity_max_percent")])
            
            field = 'enable_malfunction_alerts'
            det = get_setting_details(field)
            curr = det.get("true_text") if config.get(field) else det.get("false_text")
            buttons.append([InlineKeyboardButton(text=f"{det['name']}: {curr}", callback_data=f"cb_edit_boolean {field}")])

        buttons.append([InlineKeyboardButton(text="« Back", callback_data="cb_service_menu_back")])
        
        self.bot.editMessageText(msg_id, f"✏️ Modify *{escape_markdown(svc)}*\nSelect a setting:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_change_value(self, query_id, chat_id, msg_query, *args):
        field = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        state = self.get_status(chat_id)
        if not state: return
        data = state['data']
        
        # Salva info extra per l'input successivo
        data['field_name'] = field
        data['msg_identifier'] = msg_id
        self.set_status(chat_id, "waiting_for_new_value", **data)
        
        det = get_setting_details(field)
        txt = (f"✏️ **{det['name']}**\n\n"
               f"_{escape_markdown(det['desc'])}_\n\n"
               f"Enter new value {escape_markdown(det['range_text'])}:\n"
               f"(Type /cancel to abort)")
        
        self.bot.editMessageText(msg_id, txt, parse_mode="Markdown")

    def cb_edit_boolean(self, query_id, chat_id, msg_query, *args):
        field = args[0]
        self.bot.answerCallbackQuery(query_id)
        msg_id = telepot.message_identifier(msg_query['message'])
        
        det = get_setting_details(field)
        buttons = [
            [
                InlineKeyboardButton(text=f"✅ {det.get('true_text','True')}", callback_data=f"cb_set_boolean {field} True"),
                InlineKeyboardButton(text=f"❌ {det.get('false_text','False')}", callback_data=f"cb_set_boolean {field} False")
            ],
            [InlineKeyboardButton(text="« Back", callback_data="cb_service_modify")]
        ]
        
        self.bot.editMessageText(msg_id, f"Set *{det['name']}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_set_boolean(self, query_id, chat_id, msg_query, *args):
        field, val_str = args[0], args[1]
        val = (val_str == "True")
        self.bot.answerCallbackQuery(query_id)
        self._send_config_update(chat_id, field, val, telepot.message_identifier(msg_query['message']))

    def cb_service_menu_back(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        state = self.get_status(chat_id)
        if state:
            msg_id = telepot.message_identifier(msg_query['message'])
            self.cb_show_service_options(chat_id, msg_id, state['data'])

    def cb_show_service_options(self, chat_id, msg_id, state_data):
        """Mostra il menu intermedio (Info / Modify)"""
        svc = state_data.get("service_name")
        did = state_data.get("device_id")
        buttons = [
            [InlineKeyboardButton(text="ℹ️ Show Current Info", callback_data="cb_show_current_info")],
            [InlineKeyboardButton(text="✏️ Modify Settings", callback_data="cb_service_modify")],
            [InlineKeyboardButton(text="« Back to Services", callback_data=f"cb_settings_menu {did}")]
        ]
        self.bot.editMessageText(msg_id, f"⚙️ **{escape_markdown(svc)}** Settings", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    def cb_newdevice_start(self, query_id, chat_id, msg_query, *args):
        self.bot.answerCallbackQuery(query_id)
        self.cmd_newdevice(chat_id, msg_query)

    # --- Text Input Handlers (States) ---

    def handle_mac_input(self, chat_id, msg, state_data):
        mac = msg.get("text", "").strip()
        if not is_valid_mac(mac):
            self.bot.sendMessage(chat_id, "⚠️ Invalid MAC format.")
            return
        
        dev_info = self.catalog.find_device_by_mac(normalize_mac(mac))
        if not dev_info:
            self.bot.sendMessage(chat_id, "❌ Device not found.")
            self.clear_status(chat_id)
            return
        
        did = dev_info['deviceID']
        is_assigned = dev_info.get("user_assigned", False)
        owner = dev_info.get("owner")
        
        # Utente corrente
        linked_user = self.catalog.get_user_by_chat_id(chat_id)
        
        if is_assigned:
            if linked_user and str(owner).lower() == str(linked_user['userID']).lower():
                self.bot.sendMessage(chat_id, "✅ Device already linked to you.")
                self.clear_status(chat_id)
            else:
                # Caso Node-RED: Device assegnato a qualcun altro (potresti essere tu su altro canale)
                self.bot.sendMessage(
                    chat_id, 
                    f"⚠️ Device assigned to `{escape_markdown(owner)}`.\nIf this is you, enter your **username** to link this chat:", 
                    parse_mode="Markdown"
                )
                self.set_status(chat_id, "waiting_for_username_link", device_id=did, expected_user=owner)
        else:
            if linked_user:
                # Device libero, utente esiste
                self.catalog.post(f"/users/{linked_user['userID']}/assign-device", {"device_id": did})
                self.bot.sendMessage(chat_id, "✅ Device added to your existing account.")
                self.clear_status(chat_id)
            else:
                # Device libero, utente nuovo
                self.bot.sendMessage(chat_id, "✅ Device found. Enter a **username** to register:")
                self.set_status(chat_id, "waiting_for_username", device_id=did)

    def handle_newdevice_mac(self, chat_id, msg, state_data):
        mac = msg.get("text", "").strip()
        user_id = state_data.get("user_id")
        
        dev_info = self.catalog.find_device_by_mac(normalize_mac(mac))
        if not dev_info:
            self.bot.sendMessage(chat_id, "❌ Device not found.")
            return
        
        if dev_info.get("user_assigned"):
            self.bot.sendMessage(chat_id, "❌ Device already assigned.")
        else:
            self.catalog.post(f"/users/{user_id}/assign-device", {"device_id": dev_info['deviceID']})
            self.bot.sendMessage(chat_id, "✅ Device added successfully.")
        self.clear_status(chat_id)

    def handle_username_input(self, chat_id, msg, state_data):
        username = msg.get("text", "").strip()
        if not is_valid_username(username):
            self.bot.sendMessage(chat_id, "Invalid format. Use letters/numbers.")
            return
        
        # Check duplicati
        try:
            self.catalog.get(f"/users/{username.lower()}")
            self.bot.sendMessage(chat_id, "❌ Username already taken. Try another.")
            return
        except: pass
        
        did = state_data.get("device_id")
        try:
            self.catalog.post("/users", {"userID": username.lower(), "userName": username, "telegram_chat_id": str(chat_id)})
            self.catalog.post(f"/users/{username.lower()}/assign-device", {"device_id": did})
            self.bot.sendMessage(chat_id, "✅ Registration complete!")
            self.clear_status(chat_id)
        except Exception as e:
            self.bot.sendMessage(chat_id, f"Error: {e}")

    def handle_username_link(self, chat_id, msg, state_data):
        """
        Logica Node-RED: L'utente conferma di essere il proprietario (creato altrove).
        """
        input_name = msg.get("text", "").strip()
        expected = state_data.get("expected_user")
        
        if input_name.lower() == str(expected).lower():
            try:
                self.catalog.post(f"/users/{expected}/link_telegram", {"chat_id": str(chat_id)})
                self.bot.sendMessage(chat_id, f"✅ Success! Telegram chat linked to account `{expected}`.", parse_mode="Markdown")
            except Exception as e:
                self.bot.sendMessage(chat_id, f"Link failed: {e}")
        else:
            self.bot.sendMessage(chat_id, f"❌ Incorrect username. Expected `{expected}`.", parse_mode="Markdown")
        self.clear_status(chat_id)

    def handle_device_rename_input(self, chat_id, msg, state_data):
        new_name = msg.get("text", "").strip()
        did = state_data.get("device_id")
        mid = state_data.get("msg_identifier")
        
        try:
            self.catalog.post(f"/devices/{did}/rename", {"user_device_name": new_name})
            self.bot.editMessageText(mid, f"✅ Renamed to *{escape_markdown(new_name)}*", parse_mode="Markdown")
        except Exception as e:
            self.bot.sendMessage(chat_id, f"Error: {e}")
        self.clear_status(chat_id)

    def handle_new_value_input(self, chat_id, msg, state_data):
        field = state_data.get("field_name")
        val_str = msg.get("text", "").strip()
        details = get_setting_details(field)
        
        try:
            new_val = float(val_str)
            if new_val.is_integer(): new_val = int(val_str)
            
            if "min" in details and new_val < details["min"]: raise ValueError("Value too low")
            if "max" in details and new_val > details["max"]: raise ValueError("Value too high")
            
            self._send_config_update(chat_id, field, new_val, state_data.get("msg_identifier"))
        except ValueError:
            self.bot.sendMessage(chat_id, "⚠️ Invalid value. Check constraints.")

    def _send_config_update(self, chat_id, field, value, msg_id):
        # Recupera dati completi dallo stato
        state = self.get_status(chat_id)
        data = state.get('data', {})
        did = data.get("device_id")
        svc = data.get("service_name")
        
        topic = self.config_template.format(service_name=svc, device_id=did)
        payload = {"type": "device_config_update", "device_id": did, "config": {field: value}}
        
        self.mqtt.myPublish(topic, payload)
        self.bot.editMessageText(msg_id, f"🔄 Updating *{field}* to `{value}`...", parse_mode="Markdown")
        
        # Pulisci stato temporaneo e torna in attesa di ACK
        if 'field_name' in data: del data['field_name']
        self.set_status(chat_id, "waiting_for_config", **data)

    # --- External Event Handling ---

    def handle_config_response(self, device_id, payload, topic_type):
        """Gestisce le risposte MQTT di configurazione (Data, Ack, Error)."""
        # Trova la chat che sta aspettando questo device
        target_chat = None
        state_data = None
        
        for cid, st in self.user_states.items():
            if st['state'] in ["waiting_for_config", "waiting_for_new_value"]:
                d = st.get('data', {})
                if d.get("device_id") == device_id:
                    target_chat = cid
                    state_data = d
                    break
        
        if not target_chat: return
        
        msg_id = state_data.get("msg_identifier")
        
        if topic_type == "config_data":
            # Aggiorna la config nello stato
            state_data["config"] = payload.get("config", {})
            self.set_status(target_chat, "waiting_for_config", **state_data)
            # Mostra il menu
            self.cb_show_service_options(target_chat, msg_id, state_data)
            
        elif topic_type == "config_ack":
            self.bot.editMessageText(msg_id, "✅ Settings saved successfully.")
            self.clear_status(target_chat)
            
        elif topic_type == "config_error":
            err = payload.get("error_message", "Unknown error")
            self.bot.editMessageText(msg_id, f"❌ Update failed: {err}")
            self.set_status(target_chat, "waiting_for_config", **state_data)

    def handle_my_chat_member(self, msg):
        """Gestisce blocchi del bot."""
        status = msg.get('new_chat_member', {}).get('status')
        chat_id = msg.get('chat', {}).get('id')
        if status in ['kicked', 'left']:
            print(f"[CHAT] Bot removed from {chat_id}")
            self.clear_status(chat_id)