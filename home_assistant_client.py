import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import logging
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo, Number, NumberInfo, Button, ButtonInfo

logger = logging.getLogger(__name__)

class HomeAssistantClient:
    def __init__(self, broker, port, username, password, token, api_url, config, entities, phone_controller):
        self.token = token
        self.api_url = api_url
        self.client = mqtt.Client()
        self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.retained_values = {}
        self.client.connect(broker, port, 60)
        self.client.loop_start()
        self.config = config
        self.entities = entities
        self.phone_controller = phone_controller
        logger.info("HomeAssistantClient initialized and connected to MQTT broker")

        self.device_info = DeviceInfo(
            name=config['phone_name'],
            identifiers=[config['phone_name'].lower().replace(' ', '_')],
            model=config['model'],
            manufacturer=config['manufacturer']
        )
        self.mqtt_settings = Settings.MQTT(
            host=broker,
            username=username,
            password=password,
            port=port
        )

        self.setup_discovery()

    def setup_discovery(self):
        self.setup_buttons()
        self.setup_binary_sensors()
        self.setup_number_entities()

    def setup_buttons(self):
        for button in self.entities['buttons']:
            button_info = ButtonInfo(name=button['name'], device=self.device_info, unique_id=button['unique_id'])
            button_settings = Settings(mqtt=self.mqtt_settings, entity=button_info)
            button_entity = Button(button_settings, self.create_button_callback(button['callback']))
            button_entity.write_config()
            setattr(self, f"{button['unique_id']}_entity", button_entity)

    def setup_binary_sensors(self):
        for sensor in self.entities['binary_sensors']:
            sensor_info = BinarySensorInfo(
                name=sensor['name'],
                device=self.device_info,
                unique_id=sensor['unique_id'],
                entity_category="diagnostic"
            )
            sensor_settings = Settings(mqtt=self.mqtt_settings, entity=sensor_info)
            binary_sensor = BinarySensor(sensor_settings)
            binary_sensor.write_config()
            setattr(self, f"{sensor['unique_id']}_entity", binary_sensor)
            self.update_binary_sensor(sensor['unique_id'], GPIO.input(self.config[sensor['gpio_pin']]) == GPIO.HIGH)

    def setup_number_entities(self):
        for number in self.entities['number_entities']:
            number_info = NumberInfo(
                name=number['name'],
                device=self.device_info,
                unique_id=number['unique_id'],
                min=number['min'],
                max=number['max'],
                step=number['step'],
                entity_category="config",
                mode=number['mode']
            )
            number_settings = Settings(mqtt=self.mqtt_settings, entity=number_info)
            number_entity = Number(number_settings, self.create_number_callback(number['variable']))
            number_entity.write_config()
            initial_value = self.config[number['variable']]
            number_entity.set_value(initial_value)
            setattr(self, f"{number['unique_id']}_entity", number_entity)

    def create_button_callback(self, method_name):
        def callback(client, userdata, message):
            method = getattr(self.phone_controller, method_name)
            method()
        return callback

    def create_number_callback(self, variable_name):
        def callback(client, userdata, message):
            value = float(message.payload.decode())
            setattr(self.phone_controller, variable_name, value)
            number_entity = getattr(self, f"{variable_name}_entity")
            number_entity.set_value(value)  # Publish the updated value back to HA
        return callback

    def on_connect(self, client, userdata, flags, rc):
        logger.debug(f"Connected to MQTT broker with result code {rc}")
        if self.config.get('retain', False):
            self.subscribe_to_retained_values()
        self.client.publish(f"hmd/{self.config['phone_name'].lower().replace(' ', '_')}/availability", "online", retain=True)

    def on_message(self, client, userdata, message):
        topic = message.topic.split("/")[-1]
        self.retained_values[topic] = message.payload.decode()

    def subscribe_to_retained_values(self):
        for number in self.entities['number_entities']:
            self.client.subscribe(f"hmd/number/{self.config['phone_name'].lower().replace(' ', '_')}/{number['unique_id']}/state")
        # Give some time to receive retained messages
        time.sleep(2)  # Adjust this delay if needed

    def get_retained_value(self, unique_id):
        return self.retained_values.get(unique_id, None)
    
    def update_binary_sensor(self, unique_id, state):
        binary_sensor = getattr(self, f"{unique_id}_entity", None)
        if binary_sensor:
            binary_sensor.update_state(state)
