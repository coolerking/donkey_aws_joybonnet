# -*- coding: utf-8 -*-
import time
import signal
import os
import sys
from datetime import datetime

try:
    from evdev import uinput, UInput
    from evdev import ecodes as e
except ImportError:
    exit("This library requires the evdev module\n' + \
        'Install with: sudo pip install evdev")

try:
    import pigpio
except ImportError:
    exit('JoyBonnet need to install pigpio\n' + \
        'Install with: sudo apt install pigpio && ' + \
        'sudo systemctl start pigpiod && ' + \
        'sudo systemctl enable pigpiod && pip install pigpio')

class JoyBonnet:
    BOUNCE_TIME = 0.01 # Debounce time in seconds

    BUTTON_A = 12
    BUTTON_B = 6
    BUTTON_X = 16
    BUTTON_Y = 13
    SELECT   = 20
    START    = 26
    PLAYER1  = 23
    PLAYER2  = 22
    BUTTONS = [BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y, SELECT, START, PLAYER1, PLAYER2]

    ANALOG_THRESH_NEG = -600
    ANALOG_THRESH_POS = 600
    analog_states = [False, False, False, False]  # up down left right

    KEYS= { # EDIT KEYCODES IN THIS TABLE TO YOUR PREFERENCES:
	    # See /usr/include/linux/input.h for keycode names
	    # Keyboard        Bonnet        EmulationStation
	    BUTTON_A: e.KEY_LEFTCTRL, # 'A' button
	    BUTTON_B: e.KEY_LEFTALT,  # 'B' button
	    BUTTON_X: e.KEY_Z,        # 'X' button
	    BUTTON_Y: e.KEY_X,        # 'Y' button
	    SELECT:   e.KEY_SPACE,    # 'Select' button
	    START:    e.KEY_ENTER,    # 'Start' button
	    PLAYER1:  e.KEY_1,        # '#1' button         
	    PLAYER2:  e.KEY_2,        # '#2' button
	    1000:     e.KEY_UP,       # Analog up
	    1001:     e.KEY_DOWN,     # Analog down
	    1002:     e.KEY_LEFT,     # Analog left
	    1003:     e.KEY_RIGHT,    # Analog right
    }

    ###################################### ADS1015 microdriver #################################
    # Register and other configuration values:
    ADS1x15_DEFAULT_ADDRESS        = 0x48
    ADS1x15_POINTER_CONVERSION     = 0x00
    ADS1x15_POINTER_CONFIG         = 0x01

    ADS1015_REG_CONFIG_CQUE_NONE    = 0x0003 # Disable the comparator and put ALERT/RDY in high state (default)
    ADS1015_REG_CONFIG_CLAT_NONLAT  = 0x0000 # Non-latching comparator (default)
    ADS1015_REG_CONFIG_CPOL_ACTVLOW = 0x0000 # ALERT/RDY pin is low when active (default)
    ADS1015_REG_CONFIG_CMODE_TRAD   = 0x0000 # Traditional comparator with hysteresis (default)
    ADS1015_REG_CONFIG_DR_1600SPS   = 0x0080 # 1600 samples per second (default)
    ADS1015_REG_CONFIG_MODE_SINGLE  = 0x0100 # Power-down single-shot mode (default)
    ADS1015_REG_CONFIG_GAIN_ONE     = 0x0200 # gain of 1

    ADS1015_REG_CONFIG_MUX_SINGLE_0 = 0x4000 # channel 0
    ADS1015_REG_CONFIG_MUX_SINGLE_1 = 0x5000 # channel 1
    ADS1015_REG_CONFIG_MUX_SINGLE_2 = 0x6000 # channel 2
    ADS1015_REG_CONFIG_MUX_SINGLE_3 = 0x7000 # channel 3

    ADS1015_REG_CONFIG_OS_SINGLE    = 0x8000 # start a single conversion

    ADS1015_REG_CONFIG_CHANNELS = (ADS1015_REG_CONFIG_MUX_SINGLE_0, ADS1015_REG_CONFIG_MUX_SINGLE_1, 
        ADS1015_REG_CONFIG_MUX_SINGLE_2, ADS1015_REG_CONFIG_MUX_SINGLE_3)

    def __init__(self, pgio=None, i2c_bus=1, i2c_address=0x48, debug=False):
        self.debug = debug
        self.pi = pgio or pigpio.pi()
        self.handler = self.pi.i2c_open(i2c_bus, i2c_address)
        for gpio in self.BUTTONS:
            self.pi.set_mode(gpio, pigpio.INPUT)
            self.pi.set_pull_up_down(gpio, pigpio.PUD_UP)
        try:
            from evdev import ecodes as e
            self.ui = UInput({e.EV_KEY: self.KEYS.values()}, name='retrogame', bustype=e.BUS_USB)
        except ImportError:
            exit('This library requires the evdev module\n' + \
                'Install with: sudo pip install evdev')
        except uinput.UInputError as uie:
            self.log(str(uie.message))
            self.log('Have you tried running as root? sudo {}'.format(str(sys.argv[0])))
            sys.exit(0)
        for gpio in self.BUTTONS:
            self.pi.callback(gpio, pigpio.EITHER_EDGE, self.handle_button)

    def ads_read(self, channel):
        configword = self.ADS1015_REG_CONFIG_CQUE_NONE | \
            self.ADS1015_REG_CONFIG_CLAT_NONLAT | \
            self.ADS1015_REG_CONFIG_CPOL_ACTVLOW | \
            self.ADS1015_REG_CONFIG_CMODE_TRAD   |  \
            self.ADS1015_REG_CONFIG_DR_1600SPS | \
            self.ADS1015_REG_CONFIG_MODE_SINGLE  | \
            self.ADS1015_REG_CONFIG_GAIN_ONE | \
            self.ADS1015_REG_CONFIG_CHANNELS[channel] | \
            self.ADS1015_REG_CONFIG_OS_SINGLE 
        configdata = [configword >> 8, configword & 0xFF]

        if self.debug:
            print("Setting config byte = 0x%02X%02X" % (configdata[0], configdata[1]))
        self.pi.i2c_write_i2c_block_data(self.handler, self.ADS1x15_POINTER_CONFIG, configdata)

        configdata = self.read_i2c_block_data(self.ADS1x15_POINTER_CONFIG, 2) 

        if self.debug:
            print("Getting config byte = 0x%02X%02X" % (configdata[0], configdata[1]))

        while True:
            try:
                configdata = self.read_i2c_block_data(self.ADS1x15_POINTER_CONFIG, 2) 
                if self.debug:
                    print("Getting config byte = 0x%02X%02X" % (configdata[0], configdata[1]))
                if (configdata[0] & 0x80):
                    break
            except:
                pass
        # read data out!
        analogdata = self.read_i2c_block_data(self.ADS1x15_POINTER_CONVERSION, 2)
        if self.debug:
            print(analogdata),
        retval = (analogdata[0] << 8) | analogdata[1]
        retval /= 16
        if self.debug:
            self.log('-> {}'.format(retval))
        return retval

    def handle_button(self, pin):
        key = self.KEYS[pin]
        time.sleep(self.BOUNCE_TIME)
        if pin >= 1000:
            state = self.analog_states[pin-1000]
        else:
            state = 0 if self.pi.read(pin) else 1
            self.ui.write(e.EV_KEY, key, state)
            self.ui.syn()
        if self.debug:
            self.log("Pin: {}, KeyCode: {}, Event: {}".format(pin, key, 'press' if state else 'release'))

    def log(self, msg):
        print('[JoyBonnet]{}: {}'.format(
            str(datetime.now()), str(msg),
        ))

    def read_i2c_block_data(self, reg, count):
        """
        SMBusのread_i2c_block_dataと戻り値を合わせるための関数。
        引数：
            reg         デバイスレジスタ
            count       読み込むバイト数
        戻り値：
            int[]       read_i2c_block_dataの戻り値はlong[]だがpython3
                        なのでint[]
        例外：
            ConnectionError エラーの場合
        """
        (b, d) = self.pi.i2c_read_i2c_block_data(self.handler, reg, count)
        if b >= 0:
            data = []
            for i in range(count):
                value = int(d[i])
                data.append(value)
            return data
        else:
            raise ConnectionError('Error:{} in i2c_read_i2c_block_data'.format(
                str(b)))

    def shutdown(self):
        self.pi.i2c_close(self.handler)
        if self.debug:
            self.log('i2c shutdown')

if __name__ == '__main__':
    joy = JoyBonnet(debug=True)
    while True:
        try:
            y = 800 - joy.ads_read(0)
            x = joy.ads_read(1) - 800
        except IOError:
            continue
        #print("(%d , %d)" % (x, y))

        if (y > joy.ANALOG_THRESH_POS) and not joy.analog_states[0]:
            joy.analog_states[0] = True
            joy.handle_button(1000)      # send UP press
        if (y < joy.ANALOG_THRESH_POS) and joy.analog_states[0]:
            joy.analog_states[0] = False
            joy.handle_button(1000)      # send UP release
        if (y < joy.ANALOG_THRESH_NEG) and not joy.analog_states[1]:
            joy.analog_states[1] = True
            joy.handle_button(1001)      # send DOWN press
        if (y > joy.ANALOG_THRESH_NEG) and joy.analog_states[1]:
            joy.analog_states[1] = False
            joy.handle_button(1001)      # send DOWN release
        if (x < joy.ANALOG_THRESH_NEG) and not joy.analog_states[2]:
            joy.analog_states[2] = True
            joy.handle_button(1002)      # send LEFT press
        if (x > joy.ANALOG_THRESH_NEG) and joy.analog_states[2]:
            joy.analog_states[2] = False
            joy.handle_button(1002)      # send LEFT release
        if (x > joy.ANALOG_THRESH_POS) and not joy.analog_states[3]:
            joy.analog_states[3] = True
            joy.handle_button(1003)      # send RIGHT press
        if (x < joy.ANALOG_THRESH_POS) and joy.analog_states[3]:
            joy.analog_states[3] = False
            joy.handle_button(1003)      # send RIGHT release

        time.sleep(0.01)