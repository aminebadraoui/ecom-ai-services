from fastapi import APIRouter
import uuid

from app.models.ad_concept import ExtractAdConceptInput
from app.models.common import TaskResponse
from app.tasks.ad_concept_tasks import extract_ad_concept
from app.core.config import settings

router = APIRouter()

@router.post("/extract-ad-concept", response_model=TaskResponse)
async def extract_ad_concept_endpoint(input_data: ExtractAdConceptInput):
    """Extract ad concept information from an image URL"""
    task_id = str(uuid.uuid4())
    
    # Send task to Celery
    extract_ad_concept.delay(input_data.image_url, task_id)
    
    return TaskResponse(
        task_id=task_id,
        message="Ad concept extraction task started. Use the task_id to check the status."
    ) 