import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import pygame
from threading import Thread, Event
import requests
import logging
import os
import yaml
import signal
import sys
import socket
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo, Number, NumberInfo, Button, ButtonInfo

# Load configuration from YAML files
with open('config.yaml', 'r') as config_file:
    config = yaml.safe_load(config_file)

with open('secrets.yaml', 'r') as secrets_file:
    secrets = yaml.safe_load(secrets_file)

with open('entities.yaml', 'r') as entities_file:
    entities = yaml.safe_load(entities_file)

# Configuration
PHONE_NAME = config['phone_name']
PHONE_NAME_UNDERSCORE = PHONE_NAME.lower().replace(' ', '_')

MQTT_BROKER = secrets['mqtt_broker']
MQTT_PORT = secrets['mqtt_port']
MQTT_USERNAME = secrets['mqtt_username']
MQTT_PASSWORD = secrets['mqtt_password']

HA_API_URL = secrets['ha_api_url']
HA_API_TOKEN = secrets['ha_api_token']

HOOK_SWITCH_PIN = config['hook_switch_pin']
DIAL_STATE_PIN = config['dial_state_pin']
PULSE_PIN = config['pulse_pin']
RINGER_CONTROL_PIN = config['ringer_control_pin']

LOG_LEVEL = getattr(logging, config['log_level'].upper(), logging.DEBUG)
RETAIN = config.get('retain', False)

ENABLE_HA_MQTT = config['enable_ha_mqtt']

# Logging configuration
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(SCRIPT_DIR, 'sounds')

# Initialize pygame for audio playback
pygame.mixer.init()
sounds = {
    "dial_tone": pygame.mixer.Sound(os.path.join(SOUNDS_DIR, "dial_tone.wav")),
    "busy_signal": pygame.mixer.Sound(os.path.join(SOUNDS_DIR, "busy_signal_2.wav")),
    "ringback": pygame.mixer.Sound(os.path.join(SOUNDS_DIR, "ringback.wav"))
}

def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.254.254.254', 1))
        ip_address = s.getsockname()[0]
    except Exception:
        ip_address = '127.0.0.1'
    finally:
        s.close()
    return ip_address

class HomeAssistantClient:
    def __init__(self, broker, port, username, password, token, api_url):
        self.token = token
        self.api_url = api_url
        self.client = mqtt.Client()
        self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.retained_values = {}
        self.client.connect(broker, port, 60)
        self.client.loop_start()
        logger.info("HomeAssistantClient initialized and connected to MQTT broker")

        self.device_info = DeviceInfo(name=PHONE_NAME, identifiers=[PHONE_NAME_UNDERSCORE], model=config['model'], manufacturer=config['manufacturer'])
        self.mqtt_settings = Settings.MQTT(host=MQTT_BROKER, username=MQTT_USERNAME, password=MQTT_PASSWORD, port=MQTT_PORT)

        self.setup_discovery()

    def setup_discovery(self):
        self.setup_buttons()
        self.setup_binary_sensors()
        self.setup_number_entities()

    def setup_buttons(self):
        for button in entities['buttons']:
            button_info = ButtonInfo(name=button['name'], device=self.device_info, unique_id=button['unique_id'])
            button_settings = Settings(mqtt=self.mqtt_settings, entity=button_info)
            button_entity = Button(button_settings, self.create_button_callback(button['callback']))
            button_entity.write_config()
            setattr(self, f"{button['unique_id']}_entity", button_entity)

    def setup_binary_sensors(self):
        for sensor in entities['binary_sensors']:
            sensor_info = BinarySensorInfo(name=sensor['name'], device=self.device_info, unique_id=sensor['unique_id'], entity_category="diagnostic")
            sensor_settings = Settings(mqtt=self.mqtt_settings, entity=sensor_info)
            binary_sensor = BinarySensor(sensor_settings)
            binary_sensor.write_config()
            initial_state = "on" if GPIO.input(eval(sensor['gpio_pin'])) == GPIO.HIGH else "off"
            binary_sensor.update_state(initial_state)
            setattr(self, f"{sensor['unique_id']}_entity", binary_sensor)

    def setup_number_entities(self):
        for number in entities['number_entities']:
            number_info = NumberInfo(name=number['name'], device=self.device_info, unique_id=number['unique_id'], min=number['min'], max=number['max'], step=number['step'], entity_category="config", mode=number['mode'])
            number_settings = Settings(mqtt=self.mqtt_settings, entity=number_info)
            number_entity = Number(number_settings, self.create_number_callback(number['variable']))
            number_entity.write_config()
            if RETAIN:
                retained_value = self.get_retained_value(number['unique_id'])
                if retained_value is not None:
                    initial_value = float(retained_value)
                else:
                    initial_value = config[number['variable']]
            else:
                initial_value = config[number['variable']]
            number_entity.set_value(initial_value)
            setattr(self, f"{number['unique_id']}_entity", number_entity)

    def create_button_callback(self, method_name):
        def callback(client, userdata, message):
            method = getattr(phone_controller, method_name)
            method()
        return callback

    def create_number_callback(self, variable_name):
        def callback(client, userdata, message):
            value = float(message.payload.decode())
            setattr(phone_controller, variable_name, value)
            number_entity = getattr(self, f"{variable_name}_entity")
            number_entity.set_value(value)  # Publish the updated value back to HA
        return callback

    def on_connect(self, client, userdata, flags, rc):
        logger.debug(f"Connected to MQTT broker with result code {rc}")
        logger.debug(f"MQTT Broker: {MQTT_BROKER}, Port: {MQTT_PORT}, Username: {MQTT_USERNAME}")
        if RETAIN:
            self.subscribe_to_retained_values()
        self.client.publish(f"hmd/{PHONE_NAME_UNDERSCORE}/availability", "online", retain=True)

    def on_message(self, client, userdata, message):
        topic = message.topic.split("/")[-1]
        self.retained_values[topic] = message.payload.decode()

    def subscribe_to_retained_values(self):
        for number in entities['number_entities']:
            self.client.subscribe(f"hmd/number/{PHONE_NAME_UNDERSCORE}/{number['unique_id']}/state")
        # Give some time to receive retained messages
        time.sleep(2)  # Adjust this delay if needed

    def get_retained_value(self, unique_id):
        return self.retained_values.get(unique_id, None)

ha_actions = {
    "trigger_wyoming_button": {"service": "button/press", "data": {"entity_id": "button.wyoming_trigger"}},
    "activate_scene": {"service": "scene/turn_on", "data": {"entity_id": "scene.example_scene"}}
}

class PhoneController:
    def __init__(self):
        self.stop_event = Event()
        self.setup_gpio()
        self.on_hook = GPIO.input(HOOK_SWITCH_PIN) == GPIO.HIGH
        self.dialed_number = ""
        self.last_pulse_time = 0
        self.dial_timeout_occurred = False
        self.dial_tone_start_time = time.time()  # Initialize here to avoid AttributeError
        self.busy_signal_start_time = time.time()  # Initialize here to avoid AttributeError
        self.max_rings = config['max_rings']
        self.dial_tone_timeout = config['dial_tone_timeout']
        self.busy_signal_timeout = config['busy_signal_timeout']
        self.dial_timeout = config['dial_timeout']
        self.dial_actions = {
            "11": lambda: ha_client.call_service("trigger_wyoming_button") if ENABLE_HA_MQTT else logger.debug("Dial action 11 triggered"),
            "15": lambda: self.play_sound("ringback"),
            # Add more dial actions as needed
        }
        logger.info("PhoneController initialized")

    def setup_gpio(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(HOOK_SWITCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(DIAL_STATE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(PULSE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(RINGER_CONTROL_PIN, GPIO.OUT)
        logger.debug("GPIO setup complete")

    def play_sound(self, sound_name, loop=False):
        if sound_name in sounds:
            sounds[sound_name].play(-1 if loop else 0)
            logger.info(f"Playing sound: {sound_name} {'in loop' if loop else 'once'}")

    def stop_all_sounds(self):
        for sound in sounds.values():
            sound.stop()
        logger.info("Stopped all sounds")

    def ring_bell(self, duration):
        GPIO.output(RINGER_CONTROL_PIN, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(RINGER_CONTROL_PIN, GPIO.LOW)
        logger.info(f"Rang bell for {duration * 1000}ms")

    def handle_hook_switch_and_dial(self):
        previous_hook_state = GPIO.input(HOOK_SWITCH_PIN)
        while not self.stop_event.is_set():
            current_hook_state = GPIO.input(HOOK_SWITCH_PIN)
            if current_hook_state != previous_hook_state:
                hook_switch_state = "on-hook" if current_hook_state == GPIO.HIGH else "off-hook"
                logger.info(f"Hook switch is {hook_switch_state}")
                previous_hook_state = current_hook_state
                self.update_binary_sensor("hook_switch", hook_switch_state)

            if current_hook_state == GPIO.HIGH:
                if not self.on_hook:
                    self.on_hook = True
                    self.stop_all_sounds()
                    self.dialed_number = ""
                    self.dial_timeout_occurred = False
                    logger.info("Handset on-hook")
            else:
                if self.on_hook:
                    self.on_hook = False
                    self.play_sound("dial_tone", loop=True)
                    self.dial_tone_start_time = time.time()
                    logger.info("Handset off-hook, playing dial tone")
                elif not self.dial_timeout_occurred and (time.time() - self.dial_tone_start_time > self.dial_tone_timeout):
                    self.play_busy_signal()
                elif self.dial_timeout_occurred and (time.time() - self.busy_signal_start_time > self.busy_signal_timeout * 60):
                    self.stop_all_sounds()

                while not self.stop_event.is_set() and GPIO.input(DIAL_STATE_PIN) == GPIO.LOW:
                    time.sleep(0.1)
                if not self.stop_event.is_set() and GPIO.input(HOOK_SWITCH_PIN) == GPIO.LOW and not self.dial_timeout_occurred:
                    self.stop_all_sounds()
                    self.count_pulses()
                    time.sleep(0.2)
            time.sleep(0.1)

    def count_pulses(self):
        pulse_count = 0
        while GPIO.input(HOOK_SWITCH_PIN) == GPIO.LOW and not self.stop_event.is_set():
            if GPIO.input(PULSE_PIN) == GPIO.LOW:
                time.sleep(0.1)
                while GPIO.input(PULSE_PIN) == GPIO.LOW:
                    pulse_count += 1
                    time.sleep(0.1)
                self.dialed_number += str(pulse_count)
                self.last_pulse_time = time.time()
                logger.debug(f"Dialed digit: {pulse_count}")
                pulse_count = 0
            time.sleep(0.1)

    def check_dial_timeout(self):
        while not self.stop_event.is_set():
            if not self.on_hook and self.dialed_number and (time.time() - self.last_pulse_time > self.dial_timeout):
                logger.info(f"Complete dialed number: {self.dialed_number}")
                self.handle_dialed_number(self.dialed_number)
                self.dialed_number = ""
                if 'busy_signal' in sounds:
                    self.dial_timeout_occurred = True
            time.sleep(0.1)

    def play_busy_signal(self):
        self.stop_all_sounds()
        self.play_sound("busy_signal", loop=True)
        self.busy_signal_start_time = time.time()
        self.dial_timeout_occurred = True
        logger.info("Playing busy signal")

    def start_ringing(self):
        ring_count = 0
        logger.info("Starting ringer")
        while ring_count < self.max_rings and not self.stop_event.is_set():
            GPIO.output(RINGER_CONTROL_PIN, GPIO.HIGH)
            self.update_binary_sensor("ringer_output", "on")
            logger.debug("Ring")
            for _ in range(20):  # Loop for 2 seconds with 0.1 second intervals
                if GPIO.input(HOOK_SWITCH_PIN) == GPIO.LOW or self.stop_event.is_set():
                    GPIO.output(RINGER_CONTROL_PIN, GPIO.LOW)
                    self.update_binary_sensor("ringer_output", "off")
                    logger.info("Handset picked up or stop event set, stopping ringer")
                    return
                time.sleep(0.1)
            GPIO.output(RINGER_CONTROL_PIN, GPIO.LOW)
            self.update_binary_sensor("ringer_output", "off")
            logger.debug("Ring paused")
            for _ in range(40):  # Loop for 4 seconds with 0.1 second intervals
                if GPIO.input(HOOK_SWITCH_PIN) == GPIO.LOW or self.stop_event.is_set():
                    logger.info("Handset picked up or stop event set, stopping ringer")
                    return
                time.sleep(0.1)
            ring_count += 1
        logger.info("Ringer stopped")

    def stop_ringing(self):
        GPIO.output(RINGER_CONTROL_PIN, GPIO.LOW)
        self.update_binary_sensor("ringer_output", "off")
        logger.info("Ringer control pin set to LOW")

    def handle_dialed_number(self, number):
        action = self.dial_actions.get(number, lambda: self.play_busy_signal())
        action()
        logger.debug(f"Handled dialed number: {number}")

    def cleanup(self):
        self.stop_event.set()
        self.stop_all_sounds()
        GPIO.cleanup()
        logger.info("Cleaned up GPIO and stopped all sounds")

    def update_binary_sensor(self, unique_id, state):
        binary_sensor = getattr(ha_client, f"{unique_id}_entity", None)
        if binary_sensor:
            binary_sensor.update_state(state)

phone_controller = PhoneController()

def signal_handler(sig, frame):
    logger.info('Signal received, exiting...')
    phone_controller.cleanup()
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"Script initialized. IP address: {get_ip_address()}")
    hook_switch_state = "on-hook" if GPIO.input(HOOK_SWITCH_PIN) == GPIO.HIGH else "off-hook"
    logger.info(f"Hook switch is {hook_switch_state}")

    if ENABLE_HA_MQTT:
        global ha_client
        ha_client = HomeAssistantClient(MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, HA_API_TOKEN, HA_API_URL)

    hook_thread = Thread(target=phone_controller.handle_hook_switch_and_dial)
    timeout_thread = Thread(target=phone_controller.check_dial_timeout)
    hook_thread.start()
    timeout_thread.start()
    
    # Ring the bell after initialization
    phone_controller.ring_bell(0.3)
    
    hook_thread.join()
    timeout_thread.join()

if __name__ == "__main__":
    main()
