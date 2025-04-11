from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional

class ExtractAdConceptInput(BaseModel):
    """Input for ad concept extraction with image URL"""
    image_url: str = Field(..., description="URL to the image to analyze")

class AdConceptOutput(BaseModel):
    """Output model for ad concept extraction with a more flexible structure"""
    title: str = Field(..., description="The name or title of the ad concept template")
    summary: str = Field(..., description="A brief description of the overall ad concept template")
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="A flexible dictionary containing all other analysis details, including elements, visual flow, visual tone, best practices, etc."
    ) 