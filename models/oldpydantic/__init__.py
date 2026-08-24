# Import all Pydantic models to resolve forward references
from models.oldpydantic.request import DeconstructInput
from models.oldpydantic.response import DeconstructResult, DeconstructOutput

# Rebuild models to resolve forward references
DeconstructInput.model_rebuild()
DeconstructOutput.model_rebuild()