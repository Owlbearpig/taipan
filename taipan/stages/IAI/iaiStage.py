# -*- coding: utf-8 -*-
"""
This file is part of Taipan.

Copyright (C) 2015 - 2016 Arno Rehn <arno@arnorehn.de>

Taipan is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Taipan is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Taipan.  If not, see <http://www.gnu.org/licenses/>.
"""

from common import Manipulator, ComponentBase, action, ureg, Q_
import asyncio
from threading import Lock
from serial import Serial
from asyncioext import threaded_async, ensure_weakly_binding_future
import enum
import logging
import traitlets
import time

class IAIConnection(ComponentBase):
    def __init__(self, port=None, baudRate=38400):
        super().__init__()
        self.serial = Serial()
        self.serial.baudrate = baudRate
        self.serial.port = port
        self.serial.timeout = 1
        self._lock = Lock()

    async def __aenter__(self):
        await super().__aenter__()
        self.open()
        return self

    async def __aexit__(self, *args):
        await super().__aexit__(*args)
        self.close()

    def open(self):
        """ Opens the Connection, potentially closing an old Connection
        """
        self.close()
        self.serial.timeout = 0.2
        self.serial.open()

    def close(self):
        """ Closes the Connection.
        """
        if self.serial.isOpen():
            self.serial.close()

    def _calculateChecksum(self, command):
        return (2**16 - sum(command)) & 255

    @threaded_async
    def send(self, command):
        if isinstance(command,str):
            command = bytes(command,'ascii')

        command += b'%02X' % self._calculateChecksum(command)

        with self._lock:
            wrongAnswer = True
            while wrongAnswer:
                time.sleep(0.02)
                self.serial.reset_input_buffer()
                self.serial.write(b'\x02' + command + b'\x03')

                line = self._readline(b'\x03')
                if len(line) > 16:
                    line=line[-16:]

                if len(line) == 0  or len(line) != 16 or line[-1] != 0x03:
                    logging.debug('{} corrupted Communication, '.format(command) +
                                    'End missing: {}'.format(line))
                    continue

                line = line[1:-1] #end of text
                s = self._calculateChecksum(line[:-2])
                if s != int(line[-2:], 16):
                    logging.debug('Checksum Error: Expected: {}, got: ' +
                                  '{}'.format(int(line[-2:], 16), s))
                else:
                    wrongAnswer = False
            return line[:-2].decode('ascii')

    def _readline(self,eol):
        leneol = len(eol)
        line = bytearray()
        while True:
            c = self.serial.read(1)
            if c:
                line += c
                if line[-leneol:] == eol:
                    break
            else:
                break
        return bytes(line)


class IAIStage(Manipulator):

    class OutBits(enum.Enum):
        pos1 = 0x1
        pos2 = 0x2
        pos3 = 0x4
        pos4 = 0x8
        moveComplete = 0x10
        homeComplete = 0x20
        zone = 0x40
        alarm = 0x80

    class StatusBits(enum.Enum):
        powerStatus = 0x1
        servoStatus = 0x2
        runStatus = 0x4
        homeStatus = 0x8
        commandRefusal = 0x80

    Alarm = {
        int('00', 16): 'noAlarm',
        int('5A', 16): 'BufferOverflow',
        int('5B', 16): 'BufferFrameError',
        int('5C', 16): 'HeaderAbnormalCharacter',
        int('5D', 16): 'DelimiterAbnormalCharacter',
        int('5F', 16): 'BCCError',
        int('61', 16): 'ReceivedBadCharacter',
        int('62', 16): 'IncorrectOperand',
        int('63', 16): 'IncorrectOperand',
        int('64', 16): 'IncorrectOperand',
        int('70', 16): 'Tried to move while run status was off',
        int('74', 16): 'Tried to move during motor commutation',
        int('75', 16): 'Tried to move while homing',
        int('B1', 16): 'Position data error',
        int('B8', 16): 'Motor commutation error',
        int('B9', 16): 'Motor Commutation error',
        int('BB', 16): 'Bad encoder feedback while homing',
        int('C0', 16): 'Excess speed',
        int('C1', 16): 'Servo error',
        int('C8', 16): 'Excess current',
        int('D0', 16): 'Excess main power voltage',
        int('D1', 16): 'Excess main power over-regeneration',
        int('D8', 16): 'Deviation error',
        int('E0', 16): 'Overload',
        int('E8', 16): 'Encoder disconnect',
        int('ED', 16): 'Encoder error',
        }

    isReadyToMove = traitlets.Bool(False, read_only=True).tag(group='Status')
    isServoOn = traitlets.Bool(False, read_only=True).tag(group='Status')
    isReferenced = traitlets.Bool(False, read_only=True).tag(group='Status')
    isPowerOn = traitlets.Bool(False, read_only=True).tag(group='Status')
    isAlarmState = traitlets.Bool(False, read_only=True).tag(group='Status')
    isMoving = traitlets.Bool(True, read_only=True).tag(group='Status')
    statusMessage = traitlets.Unicode('NoError', read_only=True).tag(group='Status')

    def __init__(self, connection, axis=0, objectName=None, loop=None):
        super().__init__(objectName, loop)

        self.connection = connection
        self.axis = b'%X' % axis

        self._identification = None
        self._isMovingFuture = asyncio.Future()

        self.setPreferredUnits(ureg.mm, ureg.mm / ureg.s)

    async def __aenter__(self):
        await super().__aenter__()
        self._leadpitch = await self._getLeadPitch()
        self.velocity = await self.getVelocity()
        self._updateFuture = ensure_weakly_binding_future(self.updateStatus)

        return self

    async def __aexit__(self, *args):
        await super().__aexit__(*args)
        self._updateFuture.cancel()

    async def updateStatus(self):
        while True:
            if (self.connection is None):
                continue
            await self.singleUpdate()
            await asyncio.sleep(0.2)

    async def singleUpdate(self):

        stat = await self.connection.send(self.axis + b'n0000000000')
        self._parseStatusString(stat)

        pos = await self.connection.send(self.axis + b'R4000074000')
        pos = self._convertPositionTomm(pos[4:])

        self.set_trait('isReadyToMove',
                       bool(self._status & self.StatusBits.runStatus.value))
        self.set_trait('isServoOn',
                       bool(self._status & self.StatusBits.servoStatus.value))
        self.set_trait('isReferenced',
                       bool(self._status & self.StatusBits.homeStatus.value))
        self.set_trait('isPowerOn',
                       bool(self._status & self.StatusBits.powerStatus.value))

        self.set_trait('isMoving',
                       not bool(self._outs & self.OutBits.moveComplete.value))

        self.set_trait('isAlarmState', bool(self._alarm != 0))
        self.set_trait('statusMessage', IAIStage.Alarm.get(self._alarm))
        self.set_trait('value', pos)

        if self.isMoving:
            self.set_trait('status', self.Status.Moving)
        else:
            self.set_trait('status', self.Status.Idle)
            if not self._isMovingFuture.done():
                self._isMovingFuture.set_result(None)

    @action('Home Stage')
    async def reference(self, motorend=True):
        command = self.axis + b'o'
        if motorend:
            command += b'07'
        else:
            command += b'08'
        command += b'00000000'
        await self.connection.send(command)

    @action('Enable Servo')
    async def enableServo(self):
        if not self.isServoOn:
            await self.connection.send(self.axis + b'q1000000000')
        else:
            logging.info('servo was already on')

    @action('Disable Servo')
    async def disableServo(self):
        if self.isServoOn:
            await self.connection.send(self.axis + b'q0000000000')
        else:
            logging.info('Servo was already off')

    async def setVelocity(self, velocity=Q_(10, 'mm/s'), acceleration=0.1):

        velocity = int(velocity.to('mm/s').magnitude * 300/self._leadpitch)
        velocity = b'%04X' % velocity
        if len(velocity) > 4:
            logging.info('IAI {}: velocity too high'.format(self.axis))
            velocity = b'02EE'

        acceleration = int(acceleration * 5883.99/self._leadpitch)
        acceleration = b'%04X' % acceleration
        if len(acceleration) > 4:
            logging.info('IAI {}: acceleration too high'.format(self.axis))
            acceleration = b'0093'

        sendstr = self.axis + b'v2' + velocity + acceleration + b'0'
        await self.connection.send(sendstr)

    async def getVelocity(self):
        #seems not to work ?
        #vel = self.send(str(self.axis) +  'R4000074010')[4:]
        vel = await self.connection.send(self.axis + b'R4000004040')
        vel = vel[4:]
        vel = Q_(int(vel, 16)/300.0*self._leadpitch, 'mm/s')
        return vel

    async def _getLeadPitch(self):
        res = await self.connection.send(self.axis + b'R4000000170')
        sizes = [2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0]
        try:
            return sizes[int(res[-1])]
        except:
            logging.error('Error: Lead pitch size not in list')
            return 0

    @action('Stop Stage')
    def stop(self):
        asyncio.ensure_future(self.connection.send(self.axis + b'd0000000000'))

    @action('Reset Stage')
    async def resetStage(self):
        await self.connection.send(self.axis + b'r0300000000')

    async def moveTo(self, val: float, velocity=None):
        if velocity is not None:
            self.velocity = velocity

        await self.setVelocity(self.velocity)
        posstr = self._convertPositionToHex(val)

        if self._isMovingFuture.done():
            self._isMovingFuture = asyncio.Future()

        stat = await self.connection.send(self.axis + b'a' + posstr + b'00')
        self._parseStatusString(stat)

        await self._isMovingFuture

    def _parseStatusString(self, statusstring):
        ''' 0 'U' answer string
            1 '0' axis number
            2 'n' status inqiry result
            3+4 '07' hex value corresponding to status
            5+6 '00' hex value corresponding to Alarm
            7+8 '40' hex value corresponding to IN
            9+10 '90' hex value corresponding to OUT'''
        self._status = int(statusstring[3:5], 16)
        self._alarm = int(statusstring[5:7], 16)
        self._outs = int(statusstring[9:11], 16)

    def _convertPositionTomm(self, positionhexstr):
        if positionhexstr[0] == 'F':
            diff = int('FFFFFFFF', 16)-int(positionhexstr, 16)
            return Q_(diff*self._leadpitch/800.0, 'mm')
        else:
            return Q_(-int(positionhexstr, 16)*self._leadpitch/800.0, 'mm')

    def _convertPositionToHex(self, position):
        position = 800.0/self._leadpitch * position.to('mm').magnitude

        if position > 0:
            position = int('FFFFFFFF', 16) - position
        else:
            position = abs(position)
        position = b'%08X' % int(position)

        return position

    def printStatus(self):
        print('Ready to Move', self.isReadyToMove)
        print('Servo On', self.isServoOn)
        print('Referenced', self.isReferenced)
        print('PowerOn', self.isPowerOn)
        print('Alarm State', self.isAlarmState)
        print('OnTarget', self.isOnTarget)
        print('Status message', self.statusMessage)
        print('Position', self.value)
        print('velocity', self.velocity)
        print('----------------------------------------')
