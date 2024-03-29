# -*- coding: utf-8 -*-
"""
Created on Wed Oct 14 15:04:51 2015

@author: pumphaus
"""

from common import traits, Scan, ureg, Q_, action, TabularMeasurements2M
from common.save import DataSaver
from stages import PI
from datasources.sr7230 import SR7230
import pyvisa
import os
import traitlets
from pathlib import Path
from dummy import DummyManipulator

rm = pyvisa.ResourceManager()

if os.name == 'posix':
    PI_port = '/dev/ttyUSB0'
    Manip1_comport = '/dev/ttyUSB1'
    Manip2_comport = '/dev/ttyUSB2'
elif os.name == 'nt':
    PI_port = 'COM4'
    Manip1_comport = 'COM3'
    Manip2_comport = 'COM5'


SR7230_USB_Port = 'USB0::0x0A2D::0x0027::14043751::RAW'
SR7230_LAN_Port = "TCPIP::169.254.150.230::50000::SOCKET"


class AppRoot(TabularMeasurements2M):
    currentData = traits.DataSet(read_only=True).tag(
        name="Time domain",
        axes_labels=['Time'],
        data_label="Amplitude",
        is_power=False)

    dataSaver = traitlets.Instance(DataSaver)

    nMeasurements = traitlets.Int(1, min=1).tag(name="No. of measurements", priority=99)

    def __init__(self, objectName=None, loop=None):
        super().__init__(objectName='PI tabular measurements',
                         loop=loop)

        self.dataSaver = DataSaver(objectName="Data Saver")

        self.pi_conn = PI.Connection(PI_port)
        #self.mani_conn2 = PI.Connection(Manip2_comport)

        pi_stage = PI.AxisAtController(self.pi_conn)
        pi_stage.objectName = "PI C-867 DLine"
        pi_stage.setPreferredUnits(ureg.ps, ureg.ps / ureg.s)

        self.TimeDomainScan = Scan(objectName='TimeDomainScan')
        self.TimeDomainScan.manipulator = pi_stage
        resource = rm.open_resource(SR7230_LAN_Port)
        self.TimeDomainScan.dataSource = SR7230(resource, ethernet=True)
        self.TimeDomainScan.dataSource.objectName = "SR7230"

        self.TimeDomainScan.continuousScan = True
        self.TimeDomainScan.minimumValue = Q_(840, 'ps')
        self.TimeDomainScan.maximumValue = Q_(910, 'ps')
        self.TimeDomainScan.overscan = Q_(1, 'ps')
        self.TimeDomainScan.step = Q_(0.05, 'ps')
        self.TimeDomainScan.positioningVelocity = Q_(40, 'ps/s')
        self.TimeDomainScan.scanVelocity = Q_(1, 'ps/s')
        self.TimeDomainScan.retractAtEnd = True

        self.dataSource = self.TimeDomainScan

        self.mani_conn1 = PI.Connection(Manip1_comport)
        manipulator1 = PI.AxisAtController(self.mani_conn1)
        manipulator1.setPreferredUnits(ureg.mm, ureg.mm / ureg.s)
        manipulator1.objectName = "PI C-863 Manip"
        self.manipulator1 = manipulator1
        self.positioningVelocityM1 = Q_(4, 'mm/s')
        self.scanVelocity = Q_(4, 'mm/s')

        manipulator2 = DummyManipulator()  # PI.AxisAtController(self.mani_conn2)
        manipulator2.setPreferredUnits(ureg.mm, ureg.mm / ureg.s)
        manipulator2.objectName = 'DummyManipulator 2'
        self.manipulator2 = manipulator2
        self.positioningVelocityM1 = Q_(4, 'mm/s')
        self.scanVelocity = Q_(4, 'mm/s')

        self.dataSaver.registerManipulator(self.manipulator1, 'Position1')
        self.dataSaver.registerManipulator(self.manipulator2, 'Position2')
        self.dataSaver.registerObjectAttribute(self, 'currentMeasurementName', 'currentTableEntry')
        self.dataSaver.fileNameTemplate = '{date}-{name}-{currentTableEntry}-{Position1}-{Position2}'
        self.dataSaver.set_trait('path', Path(r''))
        self.TimeDomainScan.addDataSetReadyCallback(self.dataSaver.process)
        self.TimeDomainScan.addDataSetReadyCallback(self.setCurrentData)
        self._backAndForth = True

    def setCurrentData(self, dataSet):
        self.set_trait('currentData', dataSet)

    async def __aenter__(self):
        #await super().__aenter__()
        await self.pi_conn.__aenter__()
        await self.mani_conn1.__aenter__()
        #await self.mani_conn2.__aenter__()
        await self.manipulator1.__aenter__()
        await self.manipulator2.__aenter__()
        await self.TimeDomainScan.manipulator.__aenter__()  # pi
        await self.TimeDomainScan.dataSource.__aenter__()  # lockin


        return self

    @action("Take Tabular measurements")
    async def takeTabularScan(self):
        self.set_trait('progress2',0)#progress trait changes added by Cornelius for additional progress Information
        for x in range(self.nMeasurements):
            dataset = await self.readDataSet()
            self.set_trait('progress2',(x+1)/self.nMeasurements)

    @action("Take No. of measurements")
    async def takeSingleMeasurements(self):
        self.set_trait('progress', 0)  # progress trait changes added by Cornelius for additional progress Information
        self.set_trait('progress2', 0)
        for x in range(self.nMeasurements):
            dataset = await self.TimeDomainScan.readDataSet()
            self.set_trait('progress', (x + 1) / self.nMeasurements)
            self.set_trait('progress2', (x + 1) / self.nMeasurements)

    @action("Stop")
    async def stop(self):  # added by Cornelius to Stop both tabular scan and multiple measurements scan
        if not self._activeFuture:
            if not self.TimeDomainScan._activeFuture:
                return
            self.TimeDomainScan._activeFuture.cancel()
            return
        self._activeFuture.cancel()

    async def __aexit__(self, *args):
        await self.pi_conn.__aexit__(*args)
        await self.mani_conn1.__aexit__(*args)
        #await self.mani_conn2.__aexit__(*args)
        await self.manipulator1.__aexit__(*args)
        await self.manipulator2.__aexit__(*args)
        await self.TimeDomainScan.manipulator.__aexit__(*args)  # pi
        await self.TimeDomainScan.dataSource.__aexit__(*args)  # lockin
        await super().__aexit__(*args)
