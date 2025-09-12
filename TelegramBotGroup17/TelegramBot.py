import json
import time
import requests
import telepot
from telepot.loop import MessageLoop
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from MyMQTT import *

CATALOG_BASE_URL = "http://localhost:8001"

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
            "/registerme": {"handler": self.register_me, "help": "Registra utente come user sul catalog"},
            "/ping":  {"handler": self.ping_command,  "help": "Risponde con 'pong'"},
            "/tastiera" : {"handler": self.tastiera_command, "help": "Fa apparire una tastiera per testare query"},
            "/help":  {"handler": self.help_command,  "help": "Mostra la lista dei comandi"},
            "/registrami":  {"handler": self.registrami_command,  "help": "Registrami ai topic MQTT"},
            "/me":  {"handler": self.me_command,  "help": "Fai vedere me sul catalog"},
            "/assigndevice":  {"handler": self.assign_device_command,  "help": "Fammi vedere i free devices sul catalog"},
            "/deleteme" : {"handler": self.delete_me, "help": "Elimina utente come user sul catalog"}
        }

        self.callbacks = {
            "cb_sum": {"handler": self.cb_sum, "help": "fa una somma"},
            "cb_ask_for_name": {"handler": self.cb_ask_for_name, "help": "Chiede se si vuole rinominare il frigo "},
            "cb_yes_name_fridge" : {"handler": self.cb_yes_name_fridge, "help": "Assegna frigo con soprannome"},
            "cb_no_name_fridge" : {"handler": self.cb_no_name_fridge, "help": "Assegna frigo senza soprannome"},
        }

        self.user_statuses = {} # "chatID" : "status"

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
        if req.status_code == 201:
            self.bot.sendMessage(chat_id, "REQUEST STATUS 201")
            return req.json()
        elif req.status_code == 409:
            g = requests.get(f"{CATALOG_BASE_URL}/users/{user_id}", json = payload, timeout = 5)
            g.raise_for_status()
            self.bot.sendMessage(chat_id, "REQUEST STATUS 409")
            return g.json()
        
        self.bot.sendMessage(chat_id, "REQUEST STATUS ALTRO")
        req.raise_for_status()

    def delete_user(self, chat_id:int) -> dict:
        req = requests.delete(f"{CATALOG_BASE_URL}/users/{chat_id}", timeout=5)
        if req.status_code == 200:
            self.bot.sendMessage(chat_id, "User Deleted")
            return req.json() if req.content else {"status": "deleted"}
        elif req.status_code == 404:
            self.bot.sendMessage(chat_id, "User not found")
            return {"status": "not_found"}
        else:
            self.bot.sendMessage(chat_id, f"Errore eliminazione: {req.status_code}")
            req.raise_for_status()


    # - CALLBACK ESEMPIO
    def cb_sum(self, query_id, chat_id, msg_query, *args):
        """Somma due numeri tra gli scelti"""
        if not args:
            self.bot.answerCallbackQuery(query_id, text = "mancano i numeri", show_alert = True)
            return
        self.bot.answerCallbackQuery(query_id) # scrivere sempre per evitare clessidra

        num_list = list(map(int, args))
        self.bot.sendMessage(chat_id, text = f"Hai passato: {args}\nLa somma è: {sum(num_list)}")

    # Manca la possibilità di personalizzare il deviceName
    def cb_ask_for_name(self, query_id, chat_id, msg_query, *args):
        """Assegna user a device"""
        if not args:
            self.bot.answerCallbackQuery(query_id, text = "Missing device to assign.", show_alert = True)
            return
        
        device_id = " ".join(map(str, args))
        printa_cose(device_id)

        keyboard = InlineKeyboardMarkup(inline_keyboard =[
            [InlineKeyboardButton(text='Yes', callback_data = f'cb_yes_name_fridge {device_id}'),
             InlineKeyboardButton(text='No', callback_data = f'cb_no_name_fridge {device_id}')],
        ])
        self.bot.editMessageText((chat_id, msg_query['message']['message_id']), 'Would you like to name the fridge?', reply_markup = keyboard)

    def cb_yes_name_fridge(self, query_id, chat_id, msg_query, *args):
        device_id = " ".join(map(str, args))
        self.bot.answerCallbackQuery(query_id)
        self.user_statuses.update(
                {chat_id: {
                    "status": "wait_for_fridge_name",
                    "deviceID": device_id
                    }
                }) #INIZIO STATUS
        self.bot.sendMessage(chat_id, text = f"il tuo nome è stato inserito negli status:\nuser_statuses:\n{from_dict_to_pretty_msg(self.user_statuses)}")
        print(json.dumps(self.user_statuses, indent=2))
        self.bot.editMessageText((chat_id, msg_query['message']['message_id']), 'Inserisci nome', )


    def cb_no_name_fridge(self, query_id, chat_id, msg_query, *args):
        device_id = " ".join(map(str, args))
        payload_for_req = {
            "device_id" : device_id,
        }
        
        req = requests.post(f"{CATALOG_BASE_URL}/users/{chat_id}/assign-device", json = payload_for_req, timeout = 5)
        if req.status_code == 200: # everything ok
            string_toret = from_dict_to_pretty_msg(req.json()['device'])
            self.bot.editMessageText((chat_id, msg_query["message"]["message_id"]), f"Done!\n\n{string_toret}")
        if req.status_code == 404: # device not found
            print("ERRORE 404")
            self.bot.sendMessage(chat_id, text = from_dict_to_pretty_msg(req.json()))
        elif req.status_code == 409: # device already assigned
            print("ERRORE 409")
            self.bot.sendMessage(chat_id, text = from_dict_to_pretty_msg(req.json()))
        else:
            req.raise_for_status()




    # --- HANDLER COMANDI (accettano *args per compatibilità) ---
    def start_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        # Send greetings in chat
        greetings = "Hi! This bot is an interface for the project 'Smart Chill' of Group 17."
        self.bot.sendMessage(chat_id, greetings)
        
        # mostra help alla fine
        self.help_command(chat_id, msg)

    def register_me(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        user_info_dict = {
            "userID" : chat_id,
            "userName": msg.get("from", {}).get("first_name")
                        or msg.get("from", {}).get("last_name")
                        or msg.get("from", {}).get("username"),
        }
        # TRASFORMO DIZIONARIO IN STRINGA LEGGIBILE
        message_user_info = "\n".join(f"{key} : {value}" for key, value in user_info_dict.items())
        self.bot.sendMessage(chat_id, f"These are the informations about you:\n{message_user_info}")
        new_user_message = self.bot.sendMessage(chat_id, "Creating new user")
        self.bot.sendChatAction(chat_id, action = "typing")
        user_json = self.create_user(chat_id, user_info_dict["userName"])
        self.bot.editMessageText((chat_id, new_user_message["message_id"]), f"Created!\nHere is your info on the catalog.json:\n{from_dict_to_pretty_msg(user_json)}")

    def delete_me(self,chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        user_info_dict = {
            "userID" : chat_id,
            "userName": msg.get("from", {}).get("first_name")
                        or msg.get("from", {}).get("last_name")
                        or msg.get("from", {}).get("username"),
        }
        new_user_message = self.bot.sendMessage(chat_id, "Deleting user")
        user_json = self.delete_user(chat_id)
        self.bot.editMessageText((chat_id, new_user_message["message_id"]), f"Created!\nHere is your info on the catalog.json:\n{from_dict_to_pretty_msg(user_json)}")

    def me_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        self.bot.sendChatAction(chat_id, action = "typing")
        req = requests.get(f"{CATALOG_BASE_URL}/users/{chat_id}", timeout = 5)
        self.bot.sendMessage(chat_id, f"STATUS CODE: {req.status_code}")
        self.bot.sendMessage(chat_id, f"You are:\n{from_dict_to_pretty_msg(req.json())}")

    def assign_device_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        self.bot.sendChatAction(chat_id, action = "typing")
        req = requests.get(f"{CATALOG_BASE_URL}/devices/unassigned", timeout = 5) # returns a list
        self.bot.sendMessage(chat_id, f"STATUS CODE: {req.status_code}")
        list_of_devices = req.json()
        if len(req.json()) != 0:
            buttons = [
                [InlineKeyboardButton(text = f"{device['deviceID']}", callback_data = f'cb_ask_for_name {device["deviceID"]}')] for device in list_of_devices
                ]
            keyboard = InlineKeyboardMarkup(inline_keyboard = buttons)
            menu_devices = self.bot.sendMessage(chat_id, "Questa è la lista di device senza utenti.\nA quale device sei interessato?", reply_markup=keyboard)
        else:
            self.bot.sendMessage(chat_id, text = "Sorry, all devices are assigned.")

    def help_command(self, chat_id, msg, *args):
        lines = [f"{cmd} — {meta['help']}" for cmd, meta in self.commands.items()
                 if cmd.startswith("/")]  # evita duplicati strani
        self.bot.sendMessage(chat_id, "The commands available are:\n" + "\n".join(lines))

    def ping_command(self, chat_id, msg, *args):
         self.bot.sendMessage(chat_id, "pong")

    def tastiera_command(self, chat_id, msg, *args):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Ping', callback_data = '/ping'), InlineKeyboardButton(text='Ping2', callback_data = '/ping')],
                    [InlineKeyboardButton(text='SOMMA 5+3', callback_data = 'cb_sum 5 3'), InlineKeyboardButton(text='SOMMA 5+6', callback_data = 'cb_sum 5 6')],
                    [InlineKeyboardButton(text='Already assigned', callback_data = 'cb_assign_device SmartChill_GGHHII'), InlineKeyboardButton(text='Missing device', callback_data = 'cb_assign_device SmartChill_GJAHDJ')],
                  ])

        self.bot.sendMessage(chat_id, 'Scegli una opzione', reply_markup = keyboard)
       
    def registrami_command(self, chat_id, msg, *args):
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

        # Controllo se la chat è in qualche stato
        if chat_id in self.user_statuses:
            if self.user_statuses.get(chat_id).get("status") == "wait_for_fridge_name":
                print("SONO ENTRATO")
                fridge_name = text
                payload_for_req = {
                    "device_id": self.user_statuses.get(chat_id).get("deviceID"),
                    "device_name": fridge_name
                }
                req = requests.post(f"{CATALOG_BASE_URL}/users/{chat_id}/assign-device", json = payload_for_req, timeout = 5)
                self.user_statuses.pop(chat_id)
                print(f"Ora gli user status sono: {json.dumps(self.user_statuses, indent=2)}")
                string_toret = from_dict_to_pretty_msg(req.json()['device'])
                self.bot.sendMessage(chat_id, f"Done!\n\n{string_toret}")


                
        # Comandi: "/cmd arg1 arg2 ..."
        elif text.startswith("/"):
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
