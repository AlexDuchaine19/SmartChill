import json
from MyMQTT import *
import random
import time

class LucaPublisher:
    def __init__(self, clientID, topic):
        with open("settings_publisher.json") as f:
            self.conf = json.load(f)

        self.client = MyMQTT(clientID, self.conf["broker"], self.conf["port"], None)
        self.topic = topic
        self.__message = {
            "bn" : clientID,
            "e" : [
                {
                    "n" : "weight", "v" : "", "t" : "", "u" : "kg"
                }
            ]
        }

    def start(self):
        self.client.start()
    
    def stop(self):
        self.client.stop()

    def sendData(self):
        msg = self.__message.copy()
        msg["e"][0]["v"] = round(random.uniform(55, 85), 2)
        msg["e"][0]["t"] = time.time()
        self.client.myPublish(self.topic, msg)
        print(f"Data sent! --- ({msg['e'][0]['v']} kg - Time: {time.ctime(msg['e'][0]['t'])})")


if __name__ == "__main__":

    topic = "Group17/SmartChill/weight"
    luca_pub = LucaPublisher("simple_pub_PESO", topic)
    luca_pub.start()
    print("Hello! This is a simple publisher.")
    for times in range(2):
        time.sleep(1)
        luca_pub.sendData()
    luca_pub.stop()
    print("Finito!")