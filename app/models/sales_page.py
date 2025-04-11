from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional

class ExtractSalesPageInput(BaseModel):
    page_url: str = Field(..., description="URL to the sales page to analyze")

class SalesPageOutput(BaseModel):
    """Output model for sales page extraction with more flexible field requirements"""
    product_name: str = Field(..., description="Name of the product")
    tagline: Optional[str] = Field(default="", description="Main tagline or slogan")
    key_benefits: List[str] = Field(default_factory=list, description="Key benefits of the product")
    features: List[str] = Field(default_factory=list, description="Product features")
    problem_addressed: Optional[str] = Field(default="", description="Problem the product addresses")
    target_audience: Optional[str] = Field(default="", description="Target audience description")
    social_proof: Dict[str, Any] = Field(default_factory=dict, description="Social proof information")
    offer: Dict[str, str] = Field(default_factory=dict, description="Offer details")
    call_to_action: Optional[str] = Field(default="", description="Call to action text")
    visual_elements_to_include: List[str] = Field(default_factory=list, description="Visual elements to include in ads")
    brand_voice: Optional[str] = Field(default="", description="Brand voice description")
    compliance_notes: Optional[str] = Field(default="", description="Compliance notes")
    
    # Additional flexible field to capture any other information
    additional_info: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Any additional information not covered by other fields"
    ) 