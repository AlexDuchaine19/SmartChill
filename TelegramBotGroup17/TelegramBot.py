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
            "/ping":  {"handler": self.ping_command,  "help": "Risponde con 'pong'"},
            "/tastiera" : {"handler": self.tastiera_command, "help": "Fa apparire una tastiera"},
            "/help":  {"handler": self.help_command,  "help": "Mostra la lista dei comandi"},
            "/registrami":  {"handler": self.registrami_command,  "help": "Registrami ai topic MQTT"},
            "/me":  {"handler": self.me_command,  "help": "Fai vedere miei topic"},
            
        }

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



    # --- HANDLER COMANDI (accettano *args per compatibilità) ---
    def start_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        # Send greetings in chat
        greetings = "Hi! This bot is an interface for the project 'Smart Chill' of Group 17."
        self.bot.sendMessage(chat_id, greetings)

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
        
        # mostra help alla fine
        self.help_command(chat_id, msg)


    def me_command(self, chat_id, msg, *args):
        _content_type, _chat_type, chat_id = telepot.glance(msg)
        self.bot.sendChatAction(chat_id, action = "typing")
        req = requests.get(f"{CATALOG_BASE_URL}/users/{chat_id}", timeout = 5)
        self.bot.sendMessage(chat_id, f"STATUS CODE: {req.status_code}")
        self.bot.sendMessage(chat_id, f"You are:\n{from_dict_to_pretty_msg(req.json())}")


    def help_command(self, chat_id, msg, *args):
        lines = [f"{cmd} — {meta['help']}" for cmd, meta in self.commands.items()
                 if cmd.startswith("/")]  # evita duplicati strani
        self.bot.sendMessage(chat_id, "The commands available are:\n" + "\n".join(lines))

    def ping_command(self, chat_id, msg, *args):
         self.bot.sendMessage(chat_id, "pong")

    def tastiera_command(self, chat_id, msg, *args):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Ping', callback_data = '/ping'), InlineKeyboardButton(text='Ping2', callback_data = '/ping')],
                    [InlineKeyboardButton(text='Start', callback_data = '/start'), InlineKeyboardButton(text='Help', callback_data = '/help')],
                    [InlineKeyboardButton(text='Scryfall', url = "https://scryfall.com/")],
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

        cmd = query_data
        entry = self.commands.get(cmd)
        if entry:
            try:
                entry["handler"](chat_id, msg_query["message"],)
            except Exception as e:
                self.bot.sendMessage(chat_id, f"Errore nell'esecuzione di {cmd}: {e}")
        else:
            self.bot.sendMessage(chat_id, f"Comando sconosciuto: {cmd}. Usa /help.")

        # se voglio editare la tastiera inline dopo la query
        # self.bot.editMessageText((chat_id, message_id), text = "Opzione scelta!", reply_markup = None)
        

    def notify(self, topic, msg: bytes):
        msg_dict = json.loads(msg)
        tosend = f"NOTIFICA da {msg_dict["bn"]}\nRisorsa: {msg_dict["e"][0]["n"]}\nValore: {msg_dict["e"][0]["v"]} {msg_dict["e"][0]["u"]}."
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
