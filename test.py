# -*- coding: utf-8 -*-
"""
Created on Wed Oct 14 15:04:51 2015

@author: pumphaus
"""

from common import ComponentBase, action
from scan import Scan
from dummy import DummyManipulator, DummyContinuousDataSource, DataSet
import asyncio
from traitlets import Instance, Int


def register_notification_hooks(component, objectPath=[]):
    for name, trait in component.attributes.items():
        if (isinstance(trait, Instance) and
            issubclass(trait.klass, ComponentBase)):

            cInst = getattr(component, name)
            register_notification_hooks(cInst, objectPath + [name])
        else:
            def print_change(change):
                print("Change at {}: {}".format(objectPath, change))

            component.observe(print_change, name)


class AppRoot(Scan):

    currentData = Instance(DataSet, read_only=True).tag(name="Plot")

    def __init__(self, loop=None):
        super().__init__(objectName="Scan", loop=loop)
        self.manipulator = DummyManipulator()
        self.manipulator.objectName = "Dummy Manipulator"
        self.dataSource = DummyContinuousDataSource(manip=self.manipulator)
        self.dataSource.objectName = "Dummy DataSource"
        self.continuousScan = True
        self.set_trait('currentData', DataSet())

    @action("Take measurement")
    async def takeMeasurement(self):
        self.set_trait('currentData', await self.readDataSet())


if __name__ == '__main__':
    root = AppRoot()
    register_notification_hooks(root)

    root.minimumValue = 0
    root.maximumValue = 10
    root.step = 1
    root.positioningVelocity = 100
    root.scanVelocity = 10

    loop = asyncio.get_event_loop()
    loop.run_until_complete(root.takeMeasurement())
