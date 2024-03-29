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
import logging
from common import ComponentBase
from asyncioext import threaded_async
from serial import Serial
from threading import Lock


class Connection(ComponentBase):
    def __init__(self, port=None, baudRate=9600, enableDebug=False):
        super().__init__()
        self.port = port
        self.baudRate = baudRate
        self.serial = Serial()
        self._lock = Lock()
        self.enableDebug = enableDebug  # does logging.info(str(command)) before serial.write(command)

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
        self.serial.port = self.port
        self.serial.baudrate = self.baudRate
        self.serial.open()

    def close(self):
        """ Closes the Connection.
        """
        if self.serial.isOpen():
            self.serial.close()

    @threaded_async
    def send(self, command, *args):
        """ Send a command over the Connection. If the command is a request,
        returns the reply.

        Parameters
        ----------
        command (convertible to bytearray) : The command to be sent.

        *args : Arguments to the command.
        """

        with self._lock:
            # convert `command` to a bytearray
            if isinstance(command, str):
                command = bytearray(command, 'ascii')
            else:
                command = bytearray(command)

            isRequest = command[-1] == ord(b'?')

            for arg in args:
                if isinstance(arg, float):
                    command += b' %.6f' % arg
                else:
                    command += b' %a' % arg

            command += b'\n'

            if self.enableDebug:
                logging.info(str(command))

            self.serial.write(command)

            # no request -> no reply. just return.
            if not isRequest:
                return

            # read reply. lines ending with ' \n' are part of a multiline
            # reply.
            replyLines = []
            while len(replyLines) == 0 or replyLines[-1][-2:] == ' \n':
                replyLines.append(self.serial.readline())

            return b''.join(replyLines)
