import json
import time
import requests
import telepot
import os
from typing import Tuple, Optional
from telepot.loop import MessageLoop
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from MyMQTT import *

CATALOG_BASE_URL = os.getenv("CATALOG_BASE_URL", "http://catalog:8001")

# funzioni che mi aiutano
def printa_cose(incognita):
    print(f"\n---\n{incognita} è di tipo {type(incognita)}\n---")

def catalog_get(path, timeout=5):
    url = f"{CATALOG_BASE_URL}{path}"
    return requests.get(url, timeout=timeout)

def catalog_post(path, json=None, timeout=5):
    url = f"{CATALOG_BASE_URL}{path}"
    return requests.post(url, json=json, timeout=timeout)

def from_dict_to_pretty_msg(data: dict) -> str:
    return "\n".join(f"{key}: {value}" for key, value in data.items())



class TelegramBot:
    def __init__(self, settings_file="telegram_settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()

        # Service configuration from settings
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]

        # Telegram bot configuration
        self.token = self.settings["telegram"]["TOKEN"]
        self.bot = telepot.Bot(self.token)
        MessageLoop(self.bot, {
            'chat': self.on_chat_message,
            'callback_query' :self.on_callback_query
            }).run_as_thread()

        # --- COMMANDS DISPATCHER ---
        # Ogni entry: comando -> {"handler": funzione, "help": descrizione}
        self.commands = {
            "/start": {"handler": self.start_command, "help": "Avvia il bot e mostra le info utente"},
            "/registerme": {"handler": self.register_me_command, "help": "Registra utente come user sul catalog"},
            "/showme":  {"handler": self.show_me_command,  "help": "Fai vedere me sul catalog"},
            "/mydevices":  {"handler": self.my_devices_command,  "help": "FAI VEDERE DEVICES"},
            "/assigndevice":  {"handler": self.assign_device_command,  "help": "Fammi vedere i free devices sul catalog"},
            "/alltopics":  {"handler": self.all_topics_command,  "help": "Fammi vedere tutti i topics che catalog gestisce"},
            "/deleteme" : {"handler": self.delete_me_command, "help": "Elimina utente come user sul catalog"},
            "/help":  {"handler": self.help_command,  "help": "Mostra la lista dei comandi"},
            "/cancel" : {"handler": self.cancel_command, "help": "Cancel current status"},
            "/tastiera" : {"handler": self.tastiera_command, "help": "Fa apparire una tastiera per testare query"},
            "/ping":  {"handler": self.ping_command,  "help": "Risponde con 'pong'"},
            "/registrami_OLD":  {"handler": self.registrami_old_command,  "help": "Registrami ai topic MQTT"},
        }

        self.callbacks = {
            "cb_sum": {"handler": self.cb_sum, "help": "fa una somma"},
            "cb_ask_for_name": {"handler": self.cb_ask_for_name, "help": "Chiede se si vuole rinominare il frigo"},
            "cb_yes_name_fridge" : {"handler": self.cb_yes_name_fridge, "help": "Assegna frigo con soprannome"},
            "cb_no_name_fridge" : {"handler": self.cb_no_name_fridge, "help": "Assegna frigo senza soprannome"},
            "cb_ask_for_float_operation": {"handler": self.cb_ask_for_float_operation, "help": "Chiede numeri per operazione"},
            "cb_quit_menu": {"handler": self.cb_quit_menu, "help": "Chiude un menu inline"},
            "cb_device_menu": {"handler": self.cb_device_menu, "help": "Apre menu per device"},
            "cb_device_info": {"handler": self.cb_device_info, "help": "Fetcha info per device"},
            "cb_device_unassign": {"handler": self.cb_device_unassign, "help": "Scollega device"},
        }

        self.statuses_list = {
            "status_waiting_for_fridge_name": {"handler": self.status_waiting_for_fridge_name, "help": "Waiting for the fridge's name"},
            "status_waiting_for_float": {"handler": self.status_waiting_for_float, "help": "Waiting for num to compute"}
        }

        # chat_id -> {"state": str, "data": dict}
        self.user_statuses = {}

        self.waiting_for_device_name = {}  # chatID: deviceID       serve per rinominare il device
        # "id" : {cose}
        self.users = {}


        # MQTT configuration
        self.mqtt_client = False
        self.broker_host = self.settings["mqtt"]["brokerIP"]
        self.broker_port = self.settings["mqtt"]["brokerPort"]
        self.connected = False

        self.running = True
       
        print(f"[INIT] {self.service_id} service starting...")

    def load_settings(self):
        """Load settings from JSON file"""
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"[ERROR] Settings file {self.settings_file} not found")
            raise
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON in settings file: {e}")
            raise

    def extract_mqtt_topics(self):
        subscribe_topics, publish_topics = [], []
        for endpoint in self.service_info["endpoints"]:
            if endpoint.startswith("MQTT Subscribe: "):
                subscribe_topics.append(endpoint.replace("MQTT Subscribe: ", ""))
            elif endpoint.startswith("MQTT Publish: "):
                publish_topics.append(endpoint.replace("MQTT Publish: ", ""))
        return subscribe_topics, publish_topics

    def setup_mqtt(self):
        """Setup MQTT client and subscribe to topics from service endpoints"""
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            
            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            # Extract and subscribe to topics from service endpoints
            subscribe_topics, _ = self.extract_mqtt_topics()
            for topic in subscribe_topics:
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed to: {topic}")
            
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            return True
            
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

    # --- UTILITY COMMANDS ---

    def create_user(self, chat_id: int, username: str) -> dict:
        """
        Attempts onboard: try to create user (POST /users)
        If already existings, gets it (GET /users/user_id)
        """
        user_id = str(chat_id)
        payload = {
            "userID" : user_id,
            "userName" : username
        }
        req = requests.post(f"{CATALOG_BASE_URL}/users", json = payload, timeout = 5)
        if req.status_code == 201: # tutto ok, ritorna utente appena creato
            # self.bot.sendMessage(chat_id, "status code 201")
            return req.json()
        elif req.status_code == 409: # utente già creato, quindi faccio nuova richiesta get e passo le info
            # self.bot.sendMessage(chat_id, "status code 409")
            g = requests.get(f"{CATALOG_BASE_URL}/users/{user_id}", json = payload, timeout = 5)
            return g.json()
        else:
            self.bot.sendMessage(chat_id, "REQUEST STATUS ALTRO")
            req.raise_for_status()

    def delete_user(self, chat_id:int) -> dict:
        req = requests.delete(f"{CATALOG_BASE_URL}/users/{chat_id}", timeout=5)
        if req.status_code == 200:
            return req.json() if req.content else {"status": "deleted"}
        elif req.status_code == 404:
            self.bot.sendMessage(chat_id, "User not found")
            return {"status": "not_found"}
        else:
            self.bot.sendMessage(chat_id, f"Errore eliminazione: {req.status_code}")
            req.raise_for_status()

    def is_registered_ex(self, chat_id: int) -> Tuple[bool, Optional[str]]:
        """
        Ritorna (exists, error).
        - exists=True  se 200
        - exists=False se 404
        - error        contiene un messaggio se c'è stato un problema di rete o 5xx/altro
        """
        try:
            r = requests.get(f"{CATALOG_BASE_URL}/users/{chat_id}", timeout=5)
        except requests.RequestException as e:
            return False, f"network_error: {e}"

        if r.status_code == 200:
            return True, None
        if r.status_code == 404:
            return False, None

        return False, f"unexpected_status: {r.status_code}"


    # ---------------------------

    # --- HANDLER COMANDI (accettano *args per compatibilità) ---
    def start_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        # Send greetings in chat
        greetings = "Hi! This bot is an interface for the project 'Smart Chill' of Group 17."
        self.bot.sendMessage(chat_id, greetings)
        time.sleep(1)
        # mostra help alla fine
        self.help_command(chat_id, msg)

    def register_me_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        user_info_dict = {
            "userID" : chat_id,
            "userName": msg.get("from", {}).get("first_name")
                        or msg.get("from", {}).get("last_name")
                        or msg.get("from", {}).get("username"),
        }
        checking_message = self.bot.sendMessage(chat_id, f"Checking...")
        user_json = self.create_user(chat_id, user_info_dict["userName"])
        printa_cose(user_json)
        if user_json.get('message') == None: # Vuol dire che il request è 409
            if user_json.get('devicesList') == []:
                devices_string_toret = "None"
            else:
                devices_string_toret = user_json.get('devicesList')
            already_registered_message = (
                f"Username: {user_json.get('userName')}\n"
                f"ID: {user_json.get('userID')}\n"
                f"Devices assigned: {devices_string_toret}\n"
                f"Registration time: {user_json.get('registration_time')}"
            )
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"You were already registered with these info:\n{already_registered_message}")
        else: # vuol dire nuovo user => invia messaggio di risposta alla richiesta
           self.bot.editMessageText((chat_id, checking_message["message_id"]), f"Ok {user_json.get('user').get('userName')}, a new user has been created!\nYour ID is: {user_json.get('user').get('userID')}")

    def delete_me_command(self,chat_id, msg, *args):
        checking_message = self.bot.sendMessage(chat_id, f"Checking...")
        # CONTROLLO SE ESISTE CON HELPER
        exists, err = self.is_registered_ex(chat_id)

        if err: # Errore: avvisa l’utente
            self.bot.editMessageText((chat_id, checking_message['message_id']), "Sorry, there was an error checking your registration. Please try again.")
            return

        if not exists: # Non è registrato
            self.bot.editMessageText((chat_id, checking_message['message_id']), "You are already not registered!")
            return

        # Esiste: procedi con la cancellazione
        new_user_message = self.bot.editMessageText((chat_id, checking_message['message_id']), "Deleting user...")
        try:
            delete_response_json = self.delete_user(chat_id)
        except Exception as e:
            self.bot.editMessageText((chat_id, new_user_message["message_id"]), f"Delete failed. Please try again later.")
            return

        unassigned = delete_response_json.get("unassigned_devices", [])
        if not unassigned:
            self.bot.editMessageText((chat_id, new_user_message["message_id"]), "Deleted!\nYou had no devices to unassign.")
        else:
            devices_str = "\n".join(map(str, unassigned))
            self.bot.editMessageText((chat_id, new_user_message["message_id"]), f"Deleted!\nThe devices that now are available are:\n{devices_str}")

    def show_me_command(self, chat_id, msg, *args):
        checking_message = self.bot.sendMessage(chat_id, f"Checking...")
        req = requests.get(f"{CATALOG_BASE_URL}/users/{chat_id}", timeout = 5)
        # self.bot.sendMessage(chat_id, f"STATUS CODE: {req.status_code}")
        if req.status_code == 200: # tutto ok
            # formatto per il messaggio
            data = req.json()
            devices = data['devicesList']
            devices_str = "Nessuno" if not devices else devices
            string_toret = (
                f"UserID: {data['userID']}\n"
                f"Username: {data['userName']}\n"
                f"Devices assigned: {devices_str}\n"
                f"Registration date: {data['registration_time']}"
            )
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"These are your informations:\n{string_toret}")

        elif req.status_code == 404: # manca user
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"You are not registered yet!\nUse /registerme to register to the catalog.")
        else:
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"{req.status_code}")

    def assign_device_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        checking_message = self.bot.sendMessage(chat_id, f"Checking...")
        

        # AGGIUNGERE CONTROLLO (SOLO CHI è USER PUO' ASSEGNNARSI DEI DEVICE LIBERI)
        usersList = requests.get(f"{CATALOG_BASE_URL}/users", timeout = 5)

        if not any(user["userID"] == str(chat_id) for user in usersList.json()):
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"You are not registered yet!\nUse /registerme to register to the catalog.")
            return
        
        req = requests.get(f"{CATALOG_BASE_URL}/devices/unassigned", timeout = 5) # returns a list
        # self.bot.sendMessage(chat_id, f"STATUS CODE: {req.status_code}")
        list_of_devices = req.json()
        if len(req.json()) != 0: # Se c'è almeno un device libero
            buttons = [
                [InlineKeyboardButton(text = f"{device['deviceID']}", callback_data = f'cb_ask_for_name {device["deviceID"]}') for device in list_of_devices],
                [InlineKeyboardButton(text = "Quit menu", callback_data = 'cb_quit_menu')]
                ]
            keyboard = InlineKeyboardMarkup(inline_keyboard = buttons)
            menu_devices = self.bot.editMessageText((chat_id, checking_message['message_id']), "Questa è la lista di device senza utenti.\nA quale device sei interessato?", reply_markup=keyboard)
        else:
            self.bot.editMessageText((chat_id, checking_message['message_id']), text = "Sorry, all devices are assigned.")

    def help_command(self, chat_id, msg, *args):
        lines = [f"{cmd} — {meta['help']}" for cmd, meta in self.commands.items() if cmd.startswith("/")]
        state_entry = self.get_status(chat_id)
        if state_entry:
            lines.append(f"\nStato attuale: `{state_entry['state']}`")
        self.bot.sendMessage(chat_id, "Comandi disponibili:\n" + "\n".join(lines))

    def ping_command(self, chat_id, msg, *args):
         self.bot.sendMessage(chat_id, "pong")

    def tastiera_command(self, chat_id, msg, *args):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Ping', callback_data = '/ping')],
                    [InlineKeyboardButton(text='SOMMA', callback_data = 'cb_ask_for_float_operation sum'), InlineKeyboardButton(text='MASSIMO', callback_data = 'cb_ask_for_float_operation max')],
                  ])

        self.bot.sendMessage(chat_id, 'Scegli una opzione', reply_markup = keyboard)
       
    def registrami_old_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        base_topic = "Group17/SmartChill"
        subscribe_topics, _ = self.extract_mqtt_topics()

        if args == ():
            buttons = [[InlineKeyboardButton(text=item.removeprefix(f"{base_topic}/").capitalize(),
                                             callback_data=f"to_sub_{item}")] for item in subscribe_topics]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            self.bot.sendMessage(chat_id, "A quale topic vuoi registrarti?", reply_markup=keyboard)
        else:
            # prima: i topic grezzi (stringhe, ripulite)
            raw_topics = [str(t).strip().lstrip('/') for t in args]
            print(f"\nRAW TOPICS:\n{raw_topics}")

            # dopo: i topic con il prefisso aggiunto
            lista_topic = [f"{base_topic}/{t}" for t in raw_topics if t]
            print(f"\nLISTA TOPIC:\n{lista_topic}")

            user_info_dict = {
                "first_name" :msg.get("from").get("first_name"),
                "last_name" :msg.get("from").get("last_name"),
                "username" :msg.get("from").get("username"),
                "MQTT_subs" : lista_topic
            }

            self.users.update({str(chat_id) : user_info_dict})
            print(f"\nSELF USERS:\n{json.dumps(self.users, indent = 2)}")

            user_info = "\n".join(f"{key} : {value}" for key, value in user_info_dict.items())
            self.bot.sendMessage(chat_id, f"These are the info relevant for your sub:\n{user_info}")

            self.bot.sendMessage(chat_id, f"Here is the list of self.users:\n{json.dumps(self.users, indent = 2)}")

    def cancel_command(self, chat_id, msg, *args):
        state_entry = self.get_status(chat_id)
        if not state_entry:
            self.bot.sendMessage(chat_id, "Nessuna procedura in corso.")
            return
        state_name = state_entry["state"]
        self.clear_status(chat_id)
        self.bot.sendMessage(chat_id, f"Procedura '{state_name}' annullata.")
        print(f"SCHIACCIATO CANCEL, LISTA USER_STATUSES:\n{json.dumps(self.user_statuses)}")

    def my_devices_command(self, chat_id, msg, *args):
        checking_message = self.bot.sendMessage(chat_id, f"Checking...")
        # CONTROLLO SE ESISTE CON HELPER
        exists, err = self.is_registered_ex(chat_id)
        if err: # Errore: avvisa l’utente
            self.bot.editMessageText((chat_id, checking_message['message_id']), "Sorry, there was an error. Please try again.")
            return
        if not exists: # Non è registrato
            self.bot.editMessageText((chat_id, checking_message['message_id']), "You are not registered!")
            return
        
        #se esiste:
        device_list = requests.get(f"{CATALOG_BASE_URL}/users/{chat_id}/devices").json()
        if device_list != []:
            buttons = [[InlineKeyboardButton(text=device['user_device_name'], callback_data=f"cb_device_menu {device['deviceID']}")] for device in device_list]
            buttons.append([InlineKeyboardButton(text="Quit menu", callback_data='cb_quit_menu')])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"Here's the device list:", reply_markup = keyboard)
        else:
            self.bot.editMessageText((chat_id, checking_message['message_id']), f"You have no devices!\nUse /assigndevice to assign an unassigned device.")

    def all_topics_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        checking_message = self.bot.sendMessage(chat_id, f"Checking...")
        all_topics = requests.get(f"{CATALOG_BASE_URL}/mqtt/topics").json()
        # print(f"-----\nALL TOPICS SCHIACCIATO\n{from_dict_to_pretty_msg(all_topics)}\n-----\n")
        self.bot.editMessageText((chat_id, checking_message['message_id']), f"{from_dict_to_pretty_msg(all_topics)}")


    # --- HANDLER CALLBACK ---
    # NEI CB INIZIALIZZO LO STATO SE NECESSARIO

    def cb_sum(self, query_id, chat_id, msg_query, *args):
        """Somma due numeri tra gli scelti"""
        if not args:
            self.bot.answerCallbackQuery(query_id, text = "mancano i numeri", show_alert = True)
            return
        self.bot.answerCallbackQuery(query_id) # scrivere sempre per evitare clessidra

        num_list = list(map(int, args))
        self.bot.sendMessage(chat_id, text = f"Hai passato: {args}\nLa somma è: {sum(num_list)}")

    def cb_ask_for_name(self, query_id, chat_id, msg_query, *args):
        """Assegna user a device"""
        if not args:
            self.bot.answerCallbackQuery(query_id, text = "Missing device to assign.", show_alert = True)
            return
        
        device_id = " ".join(map(str, args))

        keyboard = InlineKeyboardMarkup(inline_keyboard =[
            [InlineKeyboardButton(text='Yes', callback_data = f'cb_yes_name_fridge {device_id}'),
             InlineKeyboardButton(text='No', callback_data = f'cb_no_name_fridge {device_id}')],
        ])
        self.bot.editMessageText((chat_id, msg_query['message']['message_id']), 'Would you like to name the fridge?', reply_markup = keyboard)

    def cb_yes_name_fridge(self, query_id, chat_id, msg_query, *args):
        device_id = " ".join(map(str, args))

        # imposta lo stato con i dati necessari
        self.set_status(chat_id, "status_waiting_for_fridge_name", deviceID=device_id)

        # guida l'utente 
        self.bot.editMessageText((chat_id, msg_query['message']['message_id']),
                                 f"Ok! Inserisci il nome da assegnare al dispositivo:\n`{device_id}`\n\nScrivi /cancel per annullare.",
                                 parse_mode="Markdown")

    def cb_no_name_fridge(self, query_id, chat_id, msg_query, *args):
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]),"Assigning device...")
        device_id = " ".join(map(str, args))
        payload_for_req = {
            "device_id" : device_id,
        }
        
        req = requests.post(f"{CATALOG_BASE_URL}/users/{chat_id}/assign-device", json = payload_for_req, timeout = 5)
        if req.status_code == 200: # everything ok
            data = req.json()
            # print(json.dumps(data, indent=2))
            string_toret = (
                f"DeviceID: {device_id}\n"
                f"Device name: {data['device']['user_device_name']}"
            )
            self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), f"Done!\n\n{string_toret}")
        if req.status_code == 404: # device not found
            print("ERRORE 404")
            self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = from_dict_to_pretty_msg(req.json()))
        elif req.status_code == 409: # device already assigned
            print("ERRORE 409")
            self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = from_dict_to_pretty_msg(req.json()))
        else:
            req.raise_for_status()

    def cb_ask_for_float_operation(self, query_id, chat_id, msg_query, *args):
        operation_str = " ".join(map(str, args)).strip().lower()
        if operation_str not in ("sum", "max"):
            self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), "Operation unknown!")
            return
        self.bot.answerCallbackQuery(query_id)

        # imposto lo stato
        self.set_status(chat_id, "status_waiting_for_float", operation = operation_str)
        
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), f"Insert the numbers. (/cancel to cancel)")

    def cb_quit_menu(self, query_id, chat_id, msg_query, *args):
        """Closes a menu inline"""
        status_entry = self.get_status(chat_id)
        if status_entry:
            self.clear_status(chat_id)
            self.bot.editMessageText((chat_id, msg_query['message']['message_id']), "Menu chiuso, operazione annullata.")

        else:
            self.bot.editMessageText((chat_id, msg_query['message']['message_id']), "Menu chiuso.")

    def cb_device_menu(self, query_id, chat_id, msg_query, *args):
        device_id = " ".join(map(str, args))
        buttons = [
            [InlineKeyboardButton(text="Info", callback_data=f"cb_device_info {device_id}")],
            [InlineKeyboardButton(text="Unassign", callback_data=f"cb_device_unassign {device_id}")],
            [InlineKeyboardButton(text="Quit menu", callback_data='cb_quit_menu')]
                   ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = "What do you want to do?", reply_markup = keyboard)

    def cb_device_info(self, query_id, chat_id, msg_query, *args):
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = f"Fetching info...")
        device_id = " ".join(map(str, args))
        # chiedo info
        device_info_json = requests.get(f"{CATALOG_BASE_URL}/devices/{device_id}").json()
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = f"HERE INFO\n{from_dict_to_pretty_msg(device_info_json)}")

    def cb_device_unassign(self, query_id, chat_id, msg_query, *args):
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = f"Unassigning device...")
        device_id = " ".join(map(str, args))
        unassign_info_json = requests.post(f"{CATALOG_BASE_URL}/devices/{device_id}/unassign").json()
        # Formatto
        data = unassign_info_json
        string_toret = (
                f"{data['message']}\n"
                f"Device name: {data['previous_assignment_info']['user_device_name']}\n"
                f"Unassigned from user: {data['previous_assignment_info']['assigned_user']}\n"
            )
        self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), text = f"{string_toret}")


    # UTILITY STATUSES

    def set_status(self, chat_id: int, status_name: str, **data):
            """Imposta lo stato per una chat con i suoi dati."""
            if status_name not in self.statuses_list:
                raise ValueError(f"Stato sconosciuto: {status_name}")
            self.user_statuses[chat_id] = {"state": status_name, "data": dict(data or {})}

    def get_status(self, chat_id: int):
        """Ritorna {"state":..., "data":{...}} o None."""
        return self.user_statuses.get(chat_id)

    def clear_status(self, chat_id: int):
        """Rimuove lo stato della chat."""
        self.user_statuses.pop(chat_id, None)

    # --- HANDLER STATUS ---
    # IN QUESTI HANDLER PULISCO LO STATO (CHIUDO L'OPERAZIONE)
    def status_waiting_for_fridge_name(self, chat_id: int, msg: dict, data: dict, text: str):
        """Gestisce l'input testuale mentre si attende il nome del frigo."""
        # Se l'utente ha scritto un comando diverso da /cancel, ignora qui: /cancel è gestito a monte.
        if text.startswith("/") and text.lower() != "/cancel":
            # Lascia che /help o altri comandi vengano gestiti fuori dal flow: avvisa l'utente.
            self.bot.sendMessage(chat_id, "Se vuoi annullare la procedura, usa /cancel. Altrimenti invia il nome del dispositivo.")
            return

        fridge_name = text.strip()
        assigning_message = self.bot.sendMessage(chat_id, f"Assigning {fridge_name}...")
        device_id = data.get("deviceID")
        payload_for_req = {"device_id": device_id, "device_name": fridge_name}
        try:
            req = requests.post(f"{CATALOG_BASE_URL}/users/{chat_id}/assign-device", json=payload_for_req, timeout=5)
            req.raise_for_status()
        except Exception as e:
            self.bot.editMessageText((chat_id, assigning_message['message_id']), f"Errore durante l'assegnazione: {e}")
            return

        self.clear_status(chat_id)
        string_toret = from_dict_to_pretty_msg(req.json().get('device', {}))

        # Formatto messaggio
        data = req.json()
        string_toret = (
                f"DeviceID: {device_id}\n"
                f"Device name: {fridge_name}"
            )
        self.bot.editMessageText((chat_id, assigning_message['message_id']), f"Done!\n\n{string_toret}")

    def status_waiting_for_float(self, chat_id: int, msg: dict, data: dict, text: str):
        """Gestisce input numerico per SOMMA o MASSIMO"""
        if text.startswith("/") and text.lower() != "/cancel":
            self.bot.sendMessage(chat_id, "Se vuoi annullare la procedura, usa /cancel. Altrimenti invia i numeri.")
            return
        
        try:
            # Conversione unica: split su singolo spazio, float per ogni parte
            numbers = [float(x) for x in text.strip().split(" ")]
        except ValueError:
            self.bot.sendMessage(chat_id, "Formato non valido. Inserisci numeri separati da singoli spazi (es. `1 2 3.2 -5`).", parse_mode="Markdown")
            return

        operation = data.get("operation", "sum")
        result = sum(numbers) if operation == "sum" else max(numbers)

        self.clear_status(chat_id)
        self.bot.sendMessage(chat_id, f"Hai inserito: {' '.join(str(n) for n in numbers)}\nIl { 'somma' if operation=='sum' else 'massimo' } è: {result}")


    # --- GESTORE MESSAGGI ---
    def on_chat_message(self, msg):
        """Called whenever a CHAT MESSAGE is sent"""
        content_type, chat_type, chat_id = telepot.glance(msg)

        # contollo che sia testo
        if content_type != "text":
            self.bot.sendMessage(chat_id, "I accept only text messages for now.")
            return

        # estraggo testo
        text = msg["text"].strip()

        if text.lower().startswith("/cancel"):
            return self.cancel_command(chat_id, msg)
        
        # Controllo se la chat è in qualche stato
        status_entry = self.get_status(chat_id)
        if status_entry:
            status_name = status_entry["state"]
            entry = self.statuses_list.get(status_name)
            if not entry or "handler" not in entry or not callable(entry["handler"]):
                self.clear_status(chat_id)
                self.bot.sendMessage(chat_id, "Internal status not valid.\nStatus canceled, retry.")
                return
            try:
                entry["handler"](chat_id, msg, status_entry.get("data", {}), text)
            except Exception as e:
                self.bot.sendMessage(chat_id, f"Errore nello stato '{status_name}': {e}")
            return  # non processare come comando normale
                


                
        # Comandi: "/cmd arg1 arg2 ..."
        if text.startswith("/"):
            # normalizza e separa argomenti
            parts = text.split() # es. /help one two three -> ["/help", "one", "two", "three"]
            cmd = parts[0].lower() # "/help"
            args = parts[1:] # ["one", "two", "three"]

            entry = self.commands.get(cmd)
            if entry:
                try:
                    entry["handler"](chat_id, msg, *args)
                except Exception as e:
                    self.bot.sendMessage(chat_id, f"Errore nell'esecuzione di {cmd}: {e}")
            else:
                self.bot.sendMessage(chat_id, f"Comando sconosciuto: {cmd}. Usa /help.")

        else:
            self.bot.sendMessage(chat_id, "This is not a command!")

    # --- GESTORE CALLBACK ---
    def on_callback_query(self, msg_query):
        """Called whenever a query is sent (mainly inline buttons)"""
        query_id, from_id, query_data = telepot.glance(msg_query, flavor='callback_query')
        print('Callback Query:', query_id, from_id, query_data)
        self.bot.answerCallbackQuery(query_id, text="Ok!")
        
        # coppia messaggio/chat id per cancellarlo sul click
        message_id = msg_query['message']['message_id']
        chat_id = msg_query['message']['chat']['id']

        # controllo il tipo di query_data
        if query_data.startswith("/"): # query che richiama un comando => esegui il comando
            entry = self.commands.get(query_data)
            if entry:
                try:
                    entry["handler"](chat_id, msg_query["message"],)
                except Exception as e:
                    self.bot.sendMessage(chat_id, f"Error in the execution of {query_data}: {e}")
            else:
                self.bot.sendMessage(chat_id, f"Command unknown: {query_data}. Use /help.")
        elif query_data.startswith("cb"):
            try:
                parts = query_data.split()
                action = parts[0].lower()
                args = parts[1:]
                entry = self.callbacks.get(action)

                if not entry:
                    self.bot.answerCallbackQuery(query_id, text=f"Azione sconosciuta: {action}", show_alert=False)
                    if chat_id is not None:
                        self.bot.sendMessage(chat_id, f"Azione sconosciuta: {action}")
                    return
                
                try: #eseguo handler
                    entry["handler"](query_id, chat_id, msg_query, *args)
                except Exception as e:
                    self.bot.answerCallbackQuery(query_id, text="Errore nell'azione.", show_alert=True)
                    if chat_id is not None:
                        self.bot.sendMessage(chat_id, f"Errore in {action}: {e}")
            except Exception as e: # fallback di sicurezza
                try:
                    self.bot.answerCallbackQuery(query_id, text="Errore nella callback.", show_alert=True)
                except:
                    pass
                if chat_id is not None:
                    self.bot.sendMessage(chat_id, f"Errore nella callback: {e}")
        else:
            self.bot.sendMessage(chat_id, f"Query unknown: {query_data}.")

        

        # se voglio editare la tastiera inline dopo la query
        # self.bot.editMessageText((chat_id, message_id), text = "Opzione scelta!", reply_markup = None)
        
    # --- GESTORE MQTT ---
    def notify(self, topic, msg: bytes):
        msg_dict = json.loads(msg)
        tosend = f"NOTIFICA da {msg_dict['bn']}\nRisorsa: {msg_dict['e'][0]['n']}\nValore: {msg_dict['e'][0]['v']} {msg_dict['e'][0]['u']}."
        for user_ID in self.users:
            info = self.users[user_ID]          # <-- prendi il dict dell'utente
            if topic in info.get("MQTT_subs", []):
                self.bot.sendMessage(int(user_ID), tosend)

    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL TELEGRAM BOT SERVICE      ")
        print("=" * 60)
        print(f"\nHere are my info\n---\n\n{json.dumps(self.settings, indent=2)}\n")
        print("[INIT] Setting up MQTT connection...")
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT connection")
            return
        
        print(f"\nHere are my MQTT info\n---\n\n{self.extract_mqtt_topics()}\n")


        # Main loop - keep service alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()

    def shutdown(self):
        print(f"[SHUTDOWN] Stopping {self.service_info['serviceName']}...")
        self.running = False
        
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing MQTT: {e}")
        print(f"[SHUTDOWN] {self.service_info['serviceName']} stopped")

    # ---------------------------

def main():
    """Main entry point"""
    service = TelegramBot()
    
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
    finally:
        service.shutdown()

if __name__ == "__main__":
    main()
