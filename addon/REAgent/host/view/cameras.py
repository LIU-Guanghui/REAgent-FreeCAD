"""FreeCAD camera implementations for protocol directions."""

import math

from REAgent.protocol.observations import VIEW_DIRECTIONS

VIEW_CAMERA_SPECS = {
    "front": {"method": "viewFront", "camera_direction": dict(x=0., y=1., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
    "back": {"method": "viewRear", "camera_direction": dict(x=0., y=-1., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
    "top": {"method": "viewTop", "camera_direction": dict(x=0., y=0., z=-1.), "up_direction": dict(x=0., y=1., z=0.)},
    "bottom": {"method": "viewBottom", "camera_direction": dict(x=0., y=0., z=1.), "up_direction": dict(x=0., y=-1., z=0.)},
    "left": {"method": "viewLeft", "camera_direction": dict(x=1., y=0., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
    "right": {"method": "viewRight", "camera_direction": dict(x=-1., y=0., z=0.), "up_direction": dict(x=0., y=0., z=1.)},
}

for _sx in (1, -1):
    for _sy in (1, -1):
        for _sz in (1, -1):
            _corner = "iso_x%s_y%s_z%s" % tuple("p" if s > 0 else "m" for s in (_sx, _sy, _sz))
            VIEW_CAMERA_SPECS[_corner] = {
                "method": "setCameraOrientation",
                "eye_direction": dict(zip(("x", "y", "z"), (s / math.sqrt(3) for s in (_sx, _sy, _sz)))),
                "camera_direction": dict(zip(("x", "y", "z"), (-s / math.sqrt(3) for s in (_sx, _sy, _sz)))),
                "up_direction": dict(zip(("x", "y", "z"), (s / math.sqrt(6) for s in (-_sx * _sz, -_sy * _sz, 2)))),
            }

if set(VIEW_CAMERA_SPECS) != set(VIEW_DIRECTIONS):
    raise ValueError("Camera implementations must cover the protocol directions")
