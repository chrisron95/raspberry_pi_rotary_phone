import RPi.GPIO as GPIO
import time
import pygame
from threading import Thread, Event
import logging

logger = logging.getLogger(__name__)

class PhoneController:
    def __init__(self, config, sounds, ha_client):
        self.config = config
        self.sounds = sounds
        self.ha_client = ha_client
        self.ring_stop_event = Event()
        self.stop_event = Event()
        self.setup_gpio()
        self.on_hook = GPIO.input(config['hook_switch_pin']) == GPIO.HIGH
        self.dialed_number = ""
        self.last_pulse_time = 0
        self.dial_timeout_occurred = False
        self.dial_tone_start_time = time.time()
        self.busy_signal_start_time = time.time()
        self.max_rings = config['max_rings']
        self.dial_tone_timeout = config['dial_tone_timeout']
        self.busy_signal_timeout = config['busy_signal_timeout']
        self.dial_timeout = config['dial_timeout']
        self.dial_actions = {
            "11": lambda: self.ha_client.call_service("trigger_wyoming_button") if config['enable_ha_mqtt'] else logger.debug("Dial action 11 triggered"),
            "15": lambda: self.play_sound("ringback"),
        }
        self.sensor_states = {}
        logger.info("PhoneController initialized")

    def setup_gpio(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.config['hook_switch_pin'], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(self.config['dial_state_pin'], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(self.config['pulse_pin'], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(self.config['ringer_control_pin'], GPIO.OUT)
        logger.debug("GPIO setup complete")

    def play_sound(self, sound_name, loop=False):
        if (sound := self.sounds.get(sound_name)):
            sound.play(-1 if loop else 0)
            logger.info(f"Playing sound: {sound_name} {'in loop' if loop else 'once'}")

    def stop_all_sounds(self):
        for sound in self.sounds.values():
            sound.stop()
        logger.info("Stopped all sounds")

    def ring_bell(self, duration):
        GPIO.output(self.config['ringer_control_pin'], GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(self.config['ringer_control_pin'], GPIO.LOW)
        logger.info(f"Rang bell for {duration * 1000}ms")

    def handle_hook_switch_and_dial(self):
        previous_hook_state = GPIO.input(self.config['hook_switch_pin'])
        while not self.stop_event.is_set():
            current_hook_state = GPIO.input(self.config['hook_switch_pin'])
            if current_hook_state != previous_hook_state:
                hook_switch_state = "on-hook" if current_hook_state == GPIO.HIGH else "off-hook"
                logger.info(f"Hook switch is {hook_switch_state}")
                previous_hook_state = current_hook_state
                self.update_binary_sensor("hook_switch", "on" if current_hook_state == GPIO.LOW else "off")

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

                while not self.stop_event.is_set() and GPIO.input(self.config['dial_state_pin']) == GPIO.LOW:
                    time.sleep(0.1)
                if not self.stop_event.is_set() and GPIO.input(self.config['hook_switch_pin']) == GPIO.LOW and not self.dial_timeout_occurred:
                    self.stop_all_sounds()
                    self.count_pulses()
                    time.sleep(0.2)
            time.sleep(0.1)

    def count_pulses(self):
        pulse_count = 0
        while GPIO.input(self.config['hook_switch_pin']) == GPIO.LOW and not self.stop_event.is_set():
            if GPIO.input(self.config['pulse_pin']) == GPIO.LOW:
                time.sleep(0.1)
                while GPIO.input(self.config['pulse_pin']) == GPIO.LOW:
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
                if 'busy_signal' in self.sounds:
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
        self.ring_stop_event.clear()
        logger.info("Starting ringer")
        while ring_count < self.max_rings and not self.ring_stop_event.is_set():
            GPIO.output(self.config['ringer_control_pin'], GPIO.HIGH)
            self.update_binary_sensor("ringer_output", "on")
            logger.debug("Ring")
            for _ in range(20):  # Loop for 2 seconds with 0.1 second intervals
                if GPIO.input(self.config['hook_switch_pin']) == GPIO.LOW or self.ring_stop_event.is_set():
                    GPIO.output(self.config['ringer_control_pin'], GPIO.LOW)
                    self.update_binary_sensor("ringer_output", "off")
                    logger.info("Handset picked up or stop event set, stopping ringer")
                    return
                time.sleep(0.1)
            GPIO.output(self.config['ringer_control_pin'], GPIO.LOW)
            self.update_binary_sensor("ringer_output", "off")
            logger.debug("Ring paused")
            for _ in range(40):  # Loop for 4 seconds with 0.1 second intervals
                if GPIO.input(self.config['hook_switch_pin']) == GPIO.LOW or self.ring_stop_event.is_set():
                    logger.info("Handset picked up or stop event set, stopping ringer")
                    return
                time.sleep(0.1)
        logger.info("Ringer stopped")

    def stop_ringing(self):
        self.ring_stop_event.set()
        GPIO.output(self.config['ringer_control_pin'], GPIO.LOW)
        self.update_binary_sensor("ringer_output", "off")
        logger.info("Ringer control pin set to LOW")

    def handle_dialed_number(self, number):
        action = self.dial_actions.get(number, lambda: self.play_busy_signal())
        action()
        logger.debug(f"Handled dialed number: {number}")

    def cleanup(self):
        self.stop_event.set()
        self.ring_stop_event.set()
        self.stop_all_sounds()
        GPIO.cleanup()
        logger.info("Cleaned up GPIO and stopped all sounds")

    def update_binary_sensor(self, unique_id, state):
        binary_sensor = getattr(self.ha_client, f"{unique_id}_entity", None)
        if binary_sensor and self.sensor_states.get(unique_id) != state:
            logger.debug(f"Updating binary sensor {unique_id} to {state}")
            binary_sensor.update_state(state == "on")
            self.sensor_states[unique_id] = state
