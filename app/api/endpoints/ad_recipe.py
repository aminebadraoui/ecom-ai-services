from fastapi import APIRouter
import uuid

from app.models.ad_recipe import AdRecipeInput
from app.models.common import TaskResponse
from app.tasks.ad_recipe_tasks import generate_ad_recipe
from app.core.config import settings

router = APIRouter()

@router.post("/generate-ad-recipe", response_model=TaskResponse)
async def generate_ad_recipe_endpoint(input_data: AdRecipeInput):
    """Generate an ad recipe by combining ad concept and sales page data"""
    task_id = str(uuid.uuid4())
    
    # Send task to Celery
    generate_ad_recipe.delay(
        input_data.ad_archive_id,
        input_data.image_url, 
        input_data.sales_url,
        input_data.user_id,
        task_id
    )
    
    return TaskResponse(
        task_id=task_id,
        message="Ad recipe generation task started. Use the task_id to check the status."
    ) 