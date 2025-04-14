from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

class AdRecipeInput(BaseModel):
    """Input for ad recipe generation"""
    ad_archive_id: str = Field(..., description="Archive ID of the ad")
    image_url: str = Field(..., description="URL to the ad image to analyze")
    sales_url: str = Field(..., description="URL to the sales page to analyze")
    user_id: str = Field(..., description="ID of the user making the request")

class AdRecipeOutput(BaseModel):
    """Output model for ad recipe"""
    ad_archive_id: str = Field(..., description="Archive ID of the ad")
    image_url: str = Field(..., description="URL to the ad image")
    sales_url: str = Field(..., description="URL to the sales page")
    user_id: str = Field(..., description="ID of the user who made the request")
    ad_concept_json: Dict[str, Any] = Field(..., description="JSON output from ad concept analysis")
    sales_page_json: Dict[str, Any] = Field(..., description="JSON output from sales page analysis")
    recipe_prompt: str = Field(..., description="Generated recipe prompt for the ad") 