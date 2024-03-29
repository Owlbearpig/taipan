# -*- coding: utf-8 -*-
"""
This file is part of Taipan.

Copyright (C) 2015 - 2017 Arno Rehn <arno@arnorehn.de>

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

import asyncio
from common import Manipulator, DataSource, DataSet, action
import numpy as np
from traitlets import Bool, Float, Instance, Unicode
from copy import deepcopy
from common.traits import Quantity, Path
from common.units import Q_
import csv


class TabularMeasurements2M(DataSource):
    manipulator1 = Instance(Manipulator, allow_none=True)
    manipulator2 = Instance(Manipulator, allow_none=True)

    dataSource = Instance(DataSource, allow_none=True)

    positioningVelocityM1 = Quantity(Q_(1), help="The velocity of "
                                                 "Manipulator1 during positioning"
                                                 " movement").tag(
        name="Positioning velocity M1",
        priority=4)
    positioningVelocityM2 = Quantity(Q_(1), help="The velocity of "
                                                 "Manipulator2 during positioning"
                                                 " movement").tag(
        name="Positioning velocity M2",
        priority=4)

    active = Bool(False, read_only=True, help="Whether the tabular measurement"
                                              "is currently running").tag(
        name="Active")

    progress = Float(0, min=0, max=1, read_only=True).tag(name="Progress")
    progress2 = Float(0, min=0, max=1, read_only=True).tag(
        name="Total Progress")  # added by Cornelius for additional Table information

    tableFile = Path(None, is_file=True, is_dir=False, must_exist=True, allow_none=True).tag(
        name="Table file")

    currentMeasurementName = Unicode(read_only=True).tag(name="Current")

    def __init__(self, manipulator1: Manipulator = None,
                 manipulator2: Manipulator = None,
                 dataSource: DataSource = None, objectName: str = None,
                 loop: asyncio.BaseEventLoop = None):
        super().__init__(objectName=objectName, loop=loop)

        self.__original_class = self.__class__

        self.observe(self._setUnits, 'manipulator1')
        self.observe(self._setUnits, 'manipulator2')

        self.manipulator1 = manipulator1
        self.manipulator2 = manipulator2

        self.dataSource = dataSource

        self._activeFuture = None

    def _setUnits(self, change):
        """Copy the unit from the Manipulator to the metadata of the traits."""

        self.__class__ = self.__original_class

        manip = change['new']

        if manip is None:
            return

        positioningVelocityTraitMap = {'manipulator1': ['positioningVelocityM1'],
                                       'manipulator2': ['positioningVelocityM2']}

        traitsWithVelocityUnits = positioningVelocityTraitMap[change['name']]
        traitsWithBaseUnits = []

        baseUnits = manip.trait_metadata('value', 'preferred_units')
        velocityUnits = manip.trait_metadata('velocity', 'preferred_units')

        newTraits = {}
        for name, trait in self.traits().items():
            if name in traitsWithBaseUnits or name in traitsWithVelocityUnits:
                newTrait = deepcopy(trait)
                newTrait.metadata['preferred_units'] = baseUnits
                newTrait.default_value = 0 * baseUnits
                if newTrait.min is not None:
                    newTrait.min = 1 * baseUnits
                if name in traitsWithVelocityUnits:
                    newTrait.metadata['preferred_units'] = velocityUnits
                    newTrait.default_value = 1 * velocityUnits

                newTraits[name] = newTrait

        self.add_traits(**newTraits)

    async def _doSteppedScan(self, names, axis1, axis2):
        accumulator = []
        await self.dataSource.start()

        for i, (name, position1, position2) in enumerate(zip(names, axis1, axis2)):
            self.set_trait('currentMeasurementName', name)
            await self.manipulator1.moveTo(position1, self.positioningVelocityM1)
            await self.manipulator2.moveTo(position2, self.positioningVelocityM2)
            accumulator.append(await self.dataSource.readDataSet())
            self.set_trait('progress', (i + 1) / len(axis1))

        self.set_trait('currentMeasurementName', '')

        await self.dataSource.stop()

        axes = accumulator[0].axes.copy()
        axes.insert(0, axis2)
        axes.insert(0, axis1)
        data = np.array([dset.data.magnitude for dset in accumulator])
        data = data * accumulator[0].data.units

        return DataSet(data, axes)

    @action("Stop")
    async def stop(self):
        if not self._activeFuture:
            return

        self._activeFuture.cancel()

    def readDataSet(self):
        self._activeFuture = self._loop.create_task(self._readDataSetImpl())
        return self._activeFuture

    async def _readDataSetImpl(self):
        if not self._activeFuture:
            raise asyncio.InvalidStateError()

        if self.active:
            raise asyncio.InvalidStateError()

        if self.tableFile is None:
            raise RuntimeError("No table file specified!")

        def check_limits(manip, row_val_):
            manip_units = manip.trait_metadata('value', 'preferred_units')
            row_val_ = Q_(float(row_val_), manip_units)

            targetValueTrait = manip.class_traits()["targetValue"]
            limits = targetValueTrait.min, targetValueTrait.max

            if limits[0] and (row_val_ < limits[0]):
                raise ValueError(f"Row {i} out of bounds ({row_val_} < {limits[0]})")
            if limits[1] and (limits[1] < row_val_):
                raise ValueError(f"Row {i} out of bounds ({limits[1]} < {row_val_})")

        names = []
        axis1, axis2 = [], []

        units_manip1 = self.manipulator1.trait_metadata('value', 'preferred_units')
        units_manip2 = self.manipulator2.trait_metadata('value', 'preferred_units')

        with self.tableFile.open() as table:
            reader = csv.reader(
                # Skip comments
                (row for row in table if not row.startswith('#')),
                dialect='unix', skipinitialspace=True)
            for i, row in enumerate(reader):
                if len(row) != 3:
                    raise RuntimeError(f"Row has wrong amount of elements: '{row}'")

                names.append(row[0])

                try:
                    axis1.append(Q_(float(row[1]), units_manip1))
                except ValueError:
                    raise RuntimeError(f"Failed to convert {row[1]} to a float!")
                try:
                    axis2.append(Q_(float(row[2]), units_manip2))
                except ValueError:
                    raise RuntimeError(f"Failed to convert {row[2]} to a float!")

                check_limits(self.manipulator1, row[1])
                check_limits(self.manipulator2, row[2])

        self.set_trait('active', True)
        self.set_trait('progress', 0)

        try:
            await self.dataSource.stop()
            await self.manipulator1.waitForTargetReached()
            await self.manipulator2.waitForTargetReached()

            dataSet = await self._doSteppedScan(names, axis1, axis2)

            self._dataSetReady(dataSet)
            return dataSet

        finally:
            self._loop.create_task(self.dataSource.stop())
            self.manipulator1.stop()
            self.manipulator2.stop()
            self.set_trait('active', False)
            self._activeFuture = None
