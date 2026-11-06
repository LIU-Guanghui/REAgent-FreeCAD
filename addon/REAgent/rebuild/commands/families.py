"""The one list of command families the Controller registers, in registration order."""

from . import (body, boolean, datum, dressup, edit, extrude, hole, loft, mirror, pattern, pipe,
               primitives, revolve, sketch)

FAMILIES = (body, sketch, datum, primitives, extrude, revolve, hole, boolean, loft, pipe,
            dressup, mirror, pattern, edit)
