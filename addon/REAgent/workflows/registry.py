"""Explicit command registry used by the Source task form and Session."""

from . import drawing2cad, image2cad, stl2cad, stp2cad, text2cad

WORKFLOWS = {task.name: task for task in (stp2cad.TASK, stl2cad.TASK, drawing2cad.TASK, image2cad.TASK, text2cad.TASK)}
