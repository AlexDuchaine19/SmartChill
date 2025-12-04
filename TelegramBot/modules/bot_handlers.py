import telepot
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from modules.utils import is_valid_username, is_valid_mac, get_setting_details
from modules.catalog_client import CatalogError

class BotRequestHandler:
    def __init__(self, bot, catalog_client, mqtt_client):
        self.bot = bot
        self.catalog_client = catalog_client
        self.mqtt_client = mqtt_client
        
        self.user_states = {} 
        self.pending_config_requests = {} 

        self.commands = {
            "/start": self.cmd_start,
            "/help": self.cmd_help,
            "/mydevices": self.cmd_mydevices,
            "/newdevice": self.cmd_newdevice,
            "/showme": self.cmd_showme,
            "/deleteme": self.cmd_deleteme,
            "/cancel": self.cmd_cancel
        }

    def escape_md(self, text):
        if text is None: return "N/A"
        special_chars = r'_*[]()~`>#+-=|{}.!'
        text = str(text)
        for char in special_chars:
            text = text.replace(char, '\\' + char)
        return text

    def delete_message(self, chat_id, msg_id):
        try: self.bot.deleteMessage((chat_id, msg_id))
        except: pass

    # --- Entry Points ---

    def on_chat_message(self, msg):
        content_type, chat_type, chat_id = telepot.glance(msg)
        text = msg.get('text', '').strip()
        print(f"[BOT] Msg from {chat_id}: {text}")

        if content_type != 'text':
            self.bot.sendMessage(chat_id, "I only understand text messages.")
            return

        # 1. PRIORITÀ AL COMANDO DI CANCELLAZIONE
        if text.lower() == '/cancel':
            self.cmd_cancel(chat_id, msg)
            return

        # 2. PRIORITÀ AI COMANDI VALIDI (Fix per il blocco del menu)
        # Se l'utente digita un comando conosciuto, usciamo da qualsiasi stato e lo eseguiamo.
        if text.startswith('/'):
            cmd = text.split()[0].lower()
            if cmd in self.commands:
                # Se c'era uno stato attivo, lo cancelliamo (reset implicito)
                if chat_id in self.user_states:
                    print(f"[BOT] Command {cmd} received while in state. Clearing state.")
                    del self.user_states[chat_id]
                
                try:
                    self.commands[cmd](chat_id, msg)
                except Exception as e:
                    print(f"[ERROR] Command {cmd} failed: {e}")
                    import traceback; traceback.print_exc()
                    self.bot.sendMessage(chat_id, "⚠️ Error executing command.")
                return # Comando eseguito, esco.
            else:
                # Se è un comando sconosciuto ma siamo in uno stato di input (es. username),
                # potremmo volerlo trattare come testo? Di solito no, meglio avvisare.
                self.bot.sendMessage(chat_id, "Unknown command. Use /help.")
                return

        # 3. GESTIONE STATO (Input testuale atteso)
        # Solo se non era un comando, controlliamo se stiamo aspettando input
        state = self.user_states.get(chat_id, {}).get("state")
        if state:
            self.handle_state_input(chat_id, text, state, msg)
            return

        # 4. NESSUN COMANDO, NESSUNO STATO
        self.bot.sendMessage(chat_id, "Use commands (start with /) or reply using the menus.")

    def on_callback_query(self, msg):
        query_id, from_id, query_data = telepot.glance(msg, flavor='callback_query')
        chat_id = from_id
        try: self.bot.answerCallbackQuery(query_id)
        except: pass
        
        try:
            parts = query_data.split()
            action = parts[0]
            args = parts[1:]
            
            message_ctx = msg['message']

            if action == "cb_quit_menu": self.delete_message(chat_id, message_ctx['message_id'])
            elif action == "dev": self.show_device_menu(chat_id, args[0], message_ctx['message_id'])
            elif action == "info": self.show_device_info(chat_id, args[0], message_ctx['message_id'])
            elif action == "rename": self.ask_rename_device(chat_id, args[0], message_ctx['message_id'])
            elif action == "unassign": self.unassign_device(chat_id, args[0], message_ctx['message_id'])
            
            # Config Flow
            elif action == "conf": self.show_services_menu(chat_id, args[0], message_ctx['message_id'])
            elif action.startswith("svc"): self.show_service_actions(chat_id, args[0], args[1], message_ctx['message_id'])
            elif action.startswith("val"): self.request_config(chat_id, args[0], args[1], message_ctx['message_id'], for_edit=False)
            elif action.startswith("editmenu"): self.request_config(chat_id, args[0], args[1], message_ctx['message_id'], for_edit=True)
            
            # Edit Actions
            elif action == "ed": self.process_edit_menu_click(chat_id, args[0], message_ctx['message_id'])
            elif action == "sb": self.process_set_bool_click(chat_id, args[0], message_ctx['message_id'])
            
            # Cancel Edit Action
            elif action == "cancel_edit": self.cancel_edit_action(chat_id, message_ctx['message_id'])

            # Navigation
            elif action.startswith("back"): self.handle_back_nav(chat_id, action, args, message_ctx['message_id'])

        except Exception as e:
            print(f"[BOT] Callback Error: {e}")
            import traceback; traceback.print_exc()

    # --- Commands ---

    def cmd_start(self, chat_id, msg):
        first_name = self.escape_md(msg['from'].get('first_name', 'User'))
        self.bot.sendMessage(chat_id, f"👋 Welcome, {first_name}\\!", parse_mode="MarkdownV2")
        linked_user = self.catalog_client.is_chat_id_linked(chat_id)
        if linked_user:
            user_esc = self.escape_md(linked_user)
            self.bot.sendMessage(chat_id, f"You are linked to user **{user_esc}**\\. Use /mydevices\\.", parse_mode="MarkdownV2")
            return
        self.bot.sendMessage(chat_id, "To link your SmartChill account, enter your fridge *MAC address*\\.\nFormat: `XX:XX:XX:XX:XX:XX`", parse_mode="MarkdownV2")
        self.user_states[chat_id] = {"state": "WAITING_MAC"}

    def cmd_help(self, chat_id, msg):
        txt = """🤖 *SmartChill Bot Commands*

/start \- Register/Login
/mydevices \- Manage fridges
/newdevice \- Add fridge
/showme \- Info
/deleteme \- Delete account
/cancel \- Stop action"""
        self.bot.sendMessage(chat_id, txt, parse_mode="MarkdownV2")

    def cmd_newdevice(self, chat_id, msg):
        linked_user = self.catalog_client.is_chat_id_linked(chat_id)
        if not linked_user:
            self.bot.sendMessage(chat_id, "Please /start to register first.")
            return
        self.bot.sendMessage(chat_id, "Enter the *MAC address* of the new fridge:", parse_mode="MarkdownV2")
        self.user_states[chat_id] = {"state": "WAITING_NEW_MAC", "data": {"user_id": linked_user}}

    def cmd_mydevices(self, chat_id, msg):
        print(f"[DEBUG] cmd_mydevices start for {chat_id}")
        
        try:
            # Debug 1: Check user link
            linked_user = self.catalog_client.is_chat_id_linked(chat_id)
            print(f"[DEBUG] linked_user result: {linked_user}")
            
            if not linked_user:
                print("[DEBUG] User not linked. Sending 'Not registered' msg.")
                self.bot.sendMessage(chat_id, "Not registered. Use /start.")
                return
            
            # Debug 2: Fetch devices
            print(f"[DEBUG] Fetching devices for user: {linked_user}")
            devices = self.catalog_client.get_user_devices(linked_user)
            print(f"[DEBUG] Devices found: {devices}")
            
            if not devices:
                print("[DEBUG] No devices list returned or empty.")
                self.bot.sendMessage(chat_id, "No devices found. Use /newdevice.")
                return
            
            # Debug 3: Build keyboard
            print("[DEBUG] Building keyboard...")
            kb = []
            for d in devices:
                name = d.get('user_device_name', d.get('deviceID', 'Unknown'))
                dev_id = d.get('deviceID')
                print(f"[DEBUG] Adding button for {dev_id} ({name})")
                kb.append([InlineKeyboardButton(text=f"🧊 {name}", callback_data=f"dev {dev_id}")])
                
            kb.append([InlineKeyboardButton(text="Close", callback_data="cb_quit_menu")])
            
            # Debug 4: Send message
            print("[DEBUG] Sending Telegram message...")
            self.bot.sendMessage(chat_id, "📱 *Your Devices:*", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")
            print("[DEBUG] Message sent successfully.")

        except CatalogError as e:
            print(f"[DEBUG] CatalogError caught: {e}")
            self.bot.sendMessage(chat_id, f"⚠️ Error fetching devices: {e}")
        except Exception as e:
            print(f"[DEBUG] Unexpected Exception in cmd_mydevices: {e}")
            import traceback
            traceback.print_exc()
            self.bot.sendMessage(chat_id, f"⚠️ Critical Error: {e}")

    def cmd_showme(self, chat_id, msg):
        linked_user = self.catalog_client.is_chat_id_linked(chat_id)
        if not linked_user: self.bot.sendMessage(chat_id, "Not registered."); return
        try:
            data = self.catalog_client.get_user(linked_user)
            devs = len(data.get('devicesList', []))
            uid = self.escape_md(data['userID'])
            uname = self.escape_md(data['userName'])
            self.bot.sendMessage(chat_id, f"👤 *Info*\nID: `{uid}`\nName: {uname}\nDevices: {devs}", parse_mode="MarkdownV2")
        except: self.bot.sendMessage(chat_id, "Error fetching info.")

    def cmd_deleteme(self, chat_id, msg):
        linked_user = self.catalog_client.is_chat_id_linked(chat_id)
        if not linked_user: self.bot.sendMessage(chat_id, "Not registered."); return
        try:
            self.catalog_client.delete_user(linked_user)
            user_esc = self.escape_md(linked_user)
            self.bot.sendMessage(chat_id, f"✅ Account *{user_esc}* deleted\\.", parse_mode="MarkdownV2")
        except: self.bot.sendMessage(chat_id, "Error deleting account.")

    def cmd_cancel(self, chat_id, msg):
        if chat_id in self.user_states: del self.user_states[chat_id]; self.bot.sendMessage(chat_id, "🚫 Operation cancelled.")
        else: self.bot.sendMessage(chat_id, "Nothing to cancel.")

    # --- State Handling Logic ---

    def handle_state_input(self, chat_id, text, state, msg):
        data = self.user_states[chat_id].get("data", {})
        data['input_msg_id'] = msg['message_id']
        
        if state == "WAITING_MAC": self.process_mac_login(chat_id, text)
        elif state == "WAITING_USERNAME": self.process_registration(chat_id, text, data)
        elif state == "WAITING_NEW_MAC": self.process_add_device(chat_id, text, data)
        elif state == "WAITING_RENAME": self.process_rename(chat_id, text, data)
        elif state == "WAITING_SETTING_VALUE": self.process_setting_update(chat_id, text, data)

    def process_mac_login(self, chat_id, mac):
        if not is_valid_mac(mac): self.bot.sendMessage(chat_id, "⚠️ Invalid MAC\\. Format: `AABBCC112233`\\.", parse_mode="MarkdownV2"); return
        try:
            device = self.catalog_client.find_device_by_mac(mac)
            if not device: self.bot.sendMessage(chat_id, "❌ MAC not found\\. Check label and try again\\.", parse_mode="MarkdownV2"); return
            device_id = device['deviceID']; assigned_user = device.get('assigned_user')
            if device.get('user_assigned'):
                linked_user = self.catalog_client.is_chat_id_linked(chat_id)
                if linked_user and assigned_user and linked_user.lower() == assigned_user.lower():
                    dev_esc = self.escape_md(device_id)
                    self.bot.sendMessage(chat_id, f"✅ Welcome back\\! Device `{dev_esc}` is yours\\.", parse_mode="MarkdownV2")
                else: self.bot.sendMessage(chat_id, "⛔️ Device registered to another user.")
                if chat_id in self.user_states: del self.user_states[chat_id]
            else:
                linked_user = self.catalog_client.is_chat_id_linked(chat_id)
                if linked_user:
                    username = linked_user 
                    try: username = self.catalog_client.get_user(linked_user).get('userName', linked_user)
                    except: pass
                    self.catalog_client.assign_device_to_user(linked_user, device_id, f"{username}'s Fridge")
                    dev_esc = self.escape_md(device_id)
                    self.bot.sendMessage(chat_id, f"✅ Device `{dev_esc}` added to your account\\.", parse_mode="MarkdownV2")
                    if chat_id in self.user_states: del self.user_states[chat_id]
                else:
                    self.user_states[chat_id] = {"state": "WAITING_USERNAME", "data": {"device_id": device_id, "mac": mac}}
                    self.bot.sendMessage(chat_id, "✅ Device found\\! Enter a *username*:", parse_mode="MarkdownV2")
        except Exception as e: self.bot.sendMessage(chat_id, f"Error: {e}"); del self.user_states[chat_id]

    def process_registration(self, chat_id, username, data):
        if not is_valid_username(username): self.bot.sendMessage(chat_id, "⚠️ Invalid username \\(3\\-32 chars\\)\\. Try again\\.", parse_mode="MarkdownV2"); return
        user_id = username.lower(); device_id = data['device_id']
        try:
            if self.catalog_client.get_user(user_id): 
                self.bot.sendMessage(chat_id, "❌ Username taken. Choose another.")
                return
        except CatalogError as e:
            if e.status_code != 404: 
                 self.bot.sendMessage(chat_id, "Error checking username.")
                 return

        try:
            self.catalog_client.register_user({"userID": user_id, "userName": username, "telegram_chat_id": str(chat_id)})
            self.catalog_client.assign_device_to_user(user_id, device_id, f"{username}'s Fridge")
            user_esc = self.escape_md(username); dev_esc = self.escape_md(device_id)
            self.bot.sendMessage(chat_id, f"✅ Registered as *{user_esc}*\\! Device `{dev_esc}` linked\\. \n To monitor your device click here \mydevices", parse_mode="MarkdownV2")
            del self.user_states[chat_id]
        except Exception as e:
            self.bot.sendMessage(chat_id, f"❌ Registration failed: {e}")

    def process_add_device(self, chat_id, mac, data):
        user_id = data['user_id']
        if not is_valid_mac(mac): self.bot.sendMessage(chat_id, "Invalid MAC."); return
        try:
            device = self.catalog_client.find_device_by_mac(mac)
            if not device: self.bot.sendMessage(chat_id, "MAC not found."); return
            if device.get('user_assigned'): self.bot.sendMessage(chat_id, "⛔️ Device already assigned.")
            else:
                username = user_id
                try: username = self.catalog_client.get_user(user_id).get('userName', user_id)
                except: pass
                self.catalog_client.assign_device_to_user(user_id, device['deviceID'], f"{username}'s New Fridge")
                self.bot.sendMessage(chat_id, "✅ Device added successfully!")
            del self.user_states[chat_id]
        except Exception as e: self.bot.sendMessage(chat_id, f"Error: {e}")

    def process_rename(self, chat_id, new_name, data):
        dev_id = data['device_id']; msg_id = data.get('msg_identifier')
        try:
            self.catalog_client.rename_device(dev_id, new_name)
            name_esc = self.escape_md(new_name)
            if 'input_msg_id' in data: self.delete_message(chat_id, data['input_msg_id'])
            if msg_id:
                self.bot.editMessageText((chat_id, msg_id), f"✅ Renamed to *{name_esc}*\\.", parse_mode="MarkdownV2")
                time.sleep(2); self.show_device_menu(chat_id, dev_id, msg_id)
            else: self.bot.sendMessage(chat_id, f"✅ Renamed to *{name_esc}*\\.", parse_mode="MarkdownV2")
        except: self.bot.sendMessage(chat_id, "Error renaming.")
        del self.user_states[chat_id]

    def process_setting_update(self, chat_id, text, data):
        svc = data["service"]; key = data["setting"]; dev = data["device"]; msg_id = data.get("msg_id")
        
        val = text
        try:
            if '.' in text: val = float(text)
            else: val = int(text)
        except: pass
        
        self.pending_config_requests[dev] = {"chat_id": chat_id, "msg_id": msg_id, "service": svc, "action": "waiting_ack"}
        self.mqtt_client.publish_service_config_update(svc, dev, {key: val})
        
        try:
            self.delete_message(chat_id, data['input_msg_id'])
            key_esc = self.escape_md(key); val_esc = self.escape_md(str(val))
            self.bot.editMessageText((chat_id, msg_id), f"🔄 Update sent for *{key_esc}* to `{val_esc}`\\.\\.\\.\n_\\(Waiting for confirmation\\)_", parse_mode="MarkdownV2")
        except: self.bot.sendMessage(chat_id, f"✅ Sent update. Waiting confirmation...")
        del self.user_states[chat_id]

    # --- Menus & Navigation ---

    def show_device_menu(self, chat_id, device_id, msg_id_to_edit=None):
        kb = [
            [InlineKeyboardButton(text="ℹ️ Info", callback_data=f"info {device_id}")],
            [InlineKeyboardButton(text="✏️ Rename", callback_data=f"rename {device_id}")],
            [InlineKeyboardButton(text="⚙️ Config", callback_data=f"conf {device_id}")],
            [InlineKeyboardButton(text="❌ Unassign", callback_data=f"unassign {device_id}")]
        ]
        kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="cb_quit_menu")])
        dev_esc = self.escape_md(device_id)
        text = f"Device: `{dev_esc}`"
        if msg_id_to_edit: self.bot.editMessageText((chat_id, msg_id_to_edit), text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")
        else: self.bot.sendMessage(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")

    def show_device_info(self, chat_id, device_id, msg_id):
        try:
            d = self.catalog_client.get_device(device_id)
            did = self.escape_md(d['deviceID'])
            mod = self.escape_md(d['model'])
            stat = self.escape_md(d.get('status','N/A'))
            txt = f"ℹ️ *Info*\nID: `{did}`\nName: {self.escape_md(d.get('user_device_name', 'N/A'))}\nModel: {mod}\nStatus: {stat}"
            kb = [[InlineKeyboardButton(text="🔙 Back", callback_data=f"dev {device_id}")]]
            self.bot.editMessageText((chat_id, msg_id), txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")
        except: self.bot.answerCallbackQuery(callback_query_id=None, text="Error fetching info", show_alert=True)

    def unassign_device(self, chat_id, device_id, msg_id):
        try:
            self.catalog_client.unassign_device(device_id)
            self.bot.editMessageText((chat_id, msg_id), "✅ Device unassigned.")
            self.cmd_mydevices(chat_id, None) 
        except: self.bot.editMessageText((chat_id, msg_id), "❌ Error unassigning.")

    def ask_rename_device(self, chat_id, device_id, msg_id):
        self.user_states[chat_id] = {"state": "WAITING_RENAME", "data": {"device_id": device_id, "msg_identifier": msg_id}}
        self.bot.editMessageText((chat_id, msg_id), "✏️ Enter new name:", parse_mode="MarkdownV2")

    def show_services_menu(self, chat_id, device_id, msg_id):
        services = [("TimerUsageControl", "⏱️ Timer"), ("FoodSpoilageControl", "🤢 Spoilage"), ("FridgeStatusControl", "❄️ Status")]
        kb = [[InlineKeyboardButton(text=lbl, callback_data=f"svc {s} {device_id}")] for s, lbl in services]
        kb.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"dev {device_id}")])
        self.bot.editMessageText((chat_id, msg_id), "Select Service to Configure:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

    def show_service_actions(self, chat_id, service, device_id, msg_id):
        kb = [
            [InlineKeyboardButton(text="📊 Show Values", callback_data=f"val {service} {device_id}")],
            [InlineKeyboardButton(text="✏️ Edit Settings", callback_data=f"editmenu {service} {device_id}")]
        ]
        kb.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"conf {device_id}")])
        svc_esc = self.escape_md(service)
        self.bot.editMessageText((chat_id, msg_id), f"Service: *{svc_esc}*", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")

    def request_config(self, chat_id, service, device_id, msg_id, for_edit=False):
        self.pending_config_requests[device_id] = {
            "chat_id": chat_id, 
            "msg_id": msg_id, 
            "service": service, 
            "for_edit": for_edit
        }
        self.mqtt_client.publish_service_config_update(service, device_id, {"request": "get_config"})
        svc_esc = self.escape_md(service)
        mode = "Editing" if for_edit else "Fetching"
        self.bot.editMessageText((chat_id, msg_id), f"🔄 {mode} config for *{svc_esc}*\\.\\.\\.\n_\\(Waiting for MQTT response\\)_", parse_mode="MarkdownV2")
    
    def process_edit_menu_click(self, chat_id, key_index_str, msg_id):
        state = self.user_states.get(chat_id, {}).get("data")
        if not state or "config" not in state: self.bot.sendMessage(chat_id, "❌ Session expired."); return
        try:
            idx = int(key_index_str); keys = state["keys_order"]
            if idx < 0 or idx >= len(keys): raise ValueError
            key = keys[idx]
        except: return
        
        state["current_key"] = key
        self.user_states[chat_id]["data"] = state
        
        service = state["service"]; device_id = state["device"]
        details = get_setting_details(key)
        
        if details['type'] == bool: self.cb_edit_boolean(chat_id, service, key, device_id, msg_id, details)
        else: self.ask_edit_value_text(chat_id, service, key, device_id, msg_id, details)

    def ask_edit_value_text(self, chat_id, service, key, device_id, msg_id, details=None):
        if not details: details = get_setting_details(key)
        
        # Salviamo la config per il cancel (logica invariata)
        old_data = self.user_states[chat_id].get("data", {})
        self.user_states[chat_id] = {
            "state": "WAITING_SETTING_VALUE", 
            "data": {
                "service": service, "setting": key, "device": device_id, "msg_id": msg_id,
                "config": old_data.get("config"), "keys_order": old_data.get("keys_order")
            }
        }
        
        # --- CORREZIONE: Aggiunta Descrizione e Range ---
        readable_name = self.escape_md(details.get('name', key))
        description = self.escape_md(details.get('desc', ''))
        range_info = self.escape_md(details.get('range_text', ''))
        
        msg = f"⌨️ *Edit {readable_name}*\n\n_{description}_\n\nEnter new value {range_info}:"
        # ------------------------------------------------
        
        kb = [[InlineKeyboardButton(text="Cancel", callback_data="cancel_edit")]]
        self.bot.editMessageText((chat_id, msg_id), msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")

    def cb_edit_boolean(self, chat_id, service, key, device_id, msg_id, details=None):
        if not details: details = get_setting_details(key)
        
        txt_true = details.get('true_text', 'Enable')
        txt_false = details.get('false_text', 'Disable')
        
        kb = [
            [
                InlineKeyboardButton(text=f"✅ {txt_true}", callback_data="sb 1"),
                InlineKeyboardButton(text=f"❌ {txt_false}", callback_data="sb 0")
            ],
            [InlineKeyboardButton(text="Cancel", callback_data="cancel_edit")]
        ]
        
        # --- CORREZIONE: Aggiunta Descrizione ---
        key_esc = self.escape_md(details.get('name', key))
        desc_esc = self.escape_md(details.get('desc', ''))
        
        msg = f"⚙️ *Set {key_esc}*\n\n_{desc_esc}_"
        # ----------------------------------------
        
        self.bot.editMessageText((chat_id, msg_id), msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")

    def process_set_bool_click(self, chat_id, val_int_str, msg_id):
        state = self.user_states.get(chat_id, {}).get("data")
        if not state or "current_key" not in state: self.bot.editMessageText((chat_id, msg_id), "❌ Session expired."); return
        key = state["current_key"]; service = state["service"]; device = state["device"]
        val = (val_int_str == '1')
        self.pending_config_requests[device] = {"chat_id": chat_id, "msg_id": msg_id, "service": service, "action": "waiting_ack"}
        self.mqtt_client.publish_service_config_update(service, device, {key: val})
        key_esc = self.escape_md(key); val_esc = "Enabled" if val else "Disabled"
        self.bot.editMessageText((chat_id, msg_id), f"🔄 Update sent for *{key_esc}* to `{val_esc}`\\.\\.\\.\n_\\(Waiting for confirmation\\)_", parse_mode="MarkdownV2")
    
    def cancel_edit_action(self, chat_id, msg_id):
        state = self.user_states.get(chat_id, {}).get("data")
        if state and "config" in state:
             # Restore from cache
             service = state["service"]; device = state["device"]; config = state["config"]
             self.user_states[chat_id] = {"state": "CONFIG_MENU", "data": state}
             self._render_edit_menu(chat_id, msg_id, service, device, config, for_edit=True)
        else:
             self.bot.sendMessage(chat_id, "Session expired.")
             self.delete_message(chat_id, msg_id)

    def handle_back_nav(self, chat_id, action, args, msg_id):
        target = action.split('_')[1] 
        if target == "dev": self.show_device_menu(chat_id, args[0], msg_id)
        elif target == "conf": self.show_services_menu(chat_id, args[0], msg_id)
        elif target == "svc": self.show_service_actions(chat_id, args[0], args[1], msg_id)

    # --- Config Response ---

    def process_config_response(self, topic, payload):
        device_id = payload.get("device_id")
        if not device_id or device_id not in self.pending_config_requests: return
        req = self.pending_config_requests.pop(device_id)
        chat_id = req["chat_id"]; msg_id = req["msg_id"]; service = req["service"]
        if "error" in payload: self.bot.editMessageText((chat_id, msg_id), f"❌ Error: {payload.get('error', 'Unknown')}"); return
        if "config_ack" in topic or payload.get("status") == "updated":
            kb = [[InlineKeyboardButton(text="🔙 Back to Menu", callback_data=f"editmenu {service} {device_id}")]]
            self.bot.editMessageText((chat_id, msg_id), "✅ Settings updated successfully\\!", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2"); return
        
        config = payload.get("config", {})
        if not config and "device_id" in payload and len(payload) > 2: config = payload
        
        self.user_states[chat_id] = {"state": "CONFIG_MENU", "data": {"service": service, "device": device_id, "msg_id": msg_id, "config": config, "keys_order": list(config.keys())}}
        self._render_edit_menu(chat_id, msg_id, service, device_id, config, req["for_edit"])

    def _render_edit_menu(self, chat_id, msg_id, service, device_id, config, for_edit):
        if for_edit:
            kb = []
            keys = list(config.keys())
            for i, k in enumerate(keys):
                if k in ['device_id', 'timestamp', 'service', 'config_version']: continue
                v = config[k]; val_str = "✅" if v is True else "❌" if v is False else str(v)
                kb.append([InlineKeyboardButton(text=f"{k}: {val_str}", callback_data=f"ed {i}")])
            kb.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"svc {service} {device_id}")])
            svc_esc = self.escape_md(service)
            self.bot.editMessageText((chat_id, msg_id), f"✏️ *Edit {svc_esc}*", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")
        else:
            svc_esc = self.escape_md(service)
            txt = f"📊 *{svc_esc} Config*\n"
            for k, v in config.items():
                if k in ['device_id', 'timestamp', 'service', 'config_version']: continue
                k_esc = self.escape_md(k); v_esc = self.escape_md(str(v))
                txt += f"`{k_esc}`: `{v_esc}`\n"
            kb = [[InlineKeyboardButton(text="🔙 Back", callback_data=f"svc {service} {device_id}")]]
            self.bot.editMessageText((chat_id, msg_id), txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="MarkdownV2")