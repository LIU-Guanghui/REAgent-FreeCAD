"""Create a part from the user's original text requirements."""

from .spec import Workflow

TASK = Workflow("text2cad", "Text to CAD", "", delivery_queries=(("entity", "feature"),))
