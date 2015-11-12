# -*- coding: utf-8 -*-
"""
Created on Wed Oct 14 11:18:53 2015

@author: Arno Rehn
"""

import asyncio
from common import DataSource, DataSet
import numpy as np
import warnings

class Scan(DataSource):
    def __init__(self, manipulator = None, dataSource = None,
                 minimumValue = 0, maximumValue = 0, step = 0):
        super().__init__()
        self.manipulator = manipulator
        self.dataSource = dataSource
        self.minimumValue = minimumValue
        self.maximumValue = maximumValue
        self.step = step
        self.continuousScan = False
        self.retractAtEnd = False

    async def _doContinuousScan(self, axis, step):
        await self.manipulator.beginScan(axis[0], axis[-1])
        self.dataSource.start()
        # move half a step over the final position to ensure a trigger
        await self.manipulator.moveTo(axis[-1] + step / 2.0)
        self.dataSource.stop()

        dataSet = await self.dataSource.readDataSet()
        dataSet.checkConsistency()
        dataSet.axes = dataSet.axes.copy()
        dataSet.axes[0] = axis

        expectedLength = len(dataSet.axes[0])

        # Oops, somehow the received amount of data does not match our
        # expectation
        if dataSet.data.shape[0] != expectedLength:
            warnings.warn("Length of recorded data set does not match "
                          "expectation. Actual length: %d, expected "
                          "length: %d - trimming." %
                          (dataSet.data.shape[0], expectedLength))
            if (dataSet.data.shape[0] < expectedLength):
                dataSet.axes[0].resize(dataSet.data.shape[0])
            else:
                dataSet.data.resize((expectedLength,) + dataSet.data.shape[1:])

        return dataSet

    async def _doSteppedScan(self, axis):
        accumulator = []
        self.dataSource.start()
        for position in axis:
            await self.manipulator.moveTo(position)
            accumulator.append(await self.dataSource.readDataSet())
        self.dataSource.stop()

        axes = accumulator[0].axes.copy()
        axes.insert(0, axis)
        data = np.array([ dset.data for dset in accumulator ])

        return DataSet(data, axes)

    async def readDataSet(self):
        self.dataSource.stop()
        await self.manipulator.waitForTargetReached()

        # ensure correct step sign
        theStep = abs(self.step)
        if (self.maximumValue < self.minimumValue):
            theStep = -theStep

        realStep, realStart, realStop = \
            await self.manipulator.configureTrigger(theStep,
                                                    self.minimumValue,
                                                    self.maximumValue)

        axis = np.arange(realStart, realStop, realStep)

        dataSet = None

        if self.continuousScan:
            dataSet = await self._doContinuousScan(axis, realStep)
        else:
            dataSet = await self._doSteppedScan(axis)

        if self.retractAtEnd:
            asyncio.ensure_future(self.manipulator.moveTo(realStart))

        return dataSet
