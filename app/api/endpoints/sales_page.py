from fastapi import APIRouter
import uuid

from app.models.sales_page import ExtractSalesPageInput
from app.models.common import TaskResponse
from app.tasks.sales_page_tasks import extract_sales_page
from app.core.config import settings

router = APIRouter()

@router.post("/extract-sales-page", response_model=TaskResponse)
async def extract_sales_page_endpoint(input_data: ExtractSalesPageInput):
    """Extract marketing information from a sales page"""
    task_id = str(uuid.uuid4())
    
    # Send task to Celery
    extract_sales_page.delay(input_data.page_url, task_id)
    
    return TaskResponse(
        task_id=task_id,
        message="Sales page extraction task started. Use the task_id to check the status."
    ) 