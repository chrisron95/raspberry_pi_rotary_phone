import os
import RPi.GPIO as GPIO
import pygame
import yaml
import logging
import signal
import sys
from threading import Thread
from home_assistant_client import HomeAssistantClient
from phone_controller import PhoneController
from utils import get_ip_address

# Load configuration from YAML files
with open('config.yaml', 'r') as config_file:
    config = yaml.safe_load(config_file)

with open('secrets.yaml', 'r') as secrets_file:
    secrets = yaml.safe_load(secrets_file)

with open('entities.yaml', 'r') as entities_file:
    entities = yaml.safe_load(entities_file)

# Configuration
LOG_LEVEL = getattr(logging, config['log_level'].upper(), logging.DEBUG)

# Logging configuration
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Initialize pygame for audio playback
pygame.mixer.init()
sounds = {
    "dial_tone": pygame.mixer.Sound(os.path.join('sounds', "dial_tone.wav")),
    "busy_signal": pygame.mixer.Sound(os.path.join('sounds', "busy_signal_2.wav")),
    "ringback": pygame.mixer.Sound(os.path.join('sounds', "ringback.wav"))
}

def signal_handler(sig, frame):
    logger.info('Signal received, exiting...')
    phone_controller.cleanup()
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config['hook_switch_pin'], GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(config['dial_state_pin'], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(config['pulse_pin'], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(config['ringer_control_pin'], GPIO.OUT)

    logger.info(f"Script initialized. IP address: {get_ip_address()}")
    hook_switch_state = "on-hook" if GPIO.input(config['hook_switch_pin']) == GPIO.HIGH else "off-hook"
    logger.info(f"Hook switch is {hook_switch_state}")

    ha_client = HomeAssistantClient(
        broker=secrets['mqtt_broker'],
        port=secrets['mqtt_port'],
        username=secrets['mqtt_username'],
        password=secrets['mqtt_password'],
        token=secrets['ha_api_token'],
        api_url=secrets['ha_api_url'],
        config=config,
        entities=entities,
        phone_controller=None  # We'll set this after creating phone_controller
    )

    global phone_controller
    phone_controller = PhoneController(config, sounds, ha_client)
    ha_client.phone_controller = phone_controller  # Now we can set it

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
