from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Query
from typing import Optional, Dict, List, Any
from pydantic import BaseModel
import json
import os
import logging
import asyncio

from app.models.ad_concept import ExtractAdConceptInput
from app.models.common import TaskResponse
from app.tasks.ad_concept_tasks import extract_ad_concept
from app.tasks.ad_analysis_workflow import analyze_ad_with_structured_workflow
from app.models.ad_concept import AdConceptOutput
from app.core.config import settings
from app.core.redis import get_redis

router = APIRouter()
logger = logging.getLogger(__name__)

class AnalysisRequest(BaseModel):
    image_url: str
    product_context: Optional[Dict[str, Any]] = None

class StructuredAnalysisResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

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

@router.post("/analyze-ad-structured", response_model=StructuredAnalysisResponse)
async def analyze_ad_with_structured_approach(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks
):
    """
    Analyze an advertisement using the structured multi-step workflow approach
    """
    try:
        # Run the structured workflow in the background
        task_id = f"structured_analysis_{os.urandom(8).hex()}"
        
        # Create a background task for the analysis
        async def run_analysis_task():
            try:
                # Get Redis client
                redis_client = await get_redis()
                
                # Set initial task status
                await redis_client.set(
                    f"task:{task_id}", 
                    json.dumps({
                        "status": "processing",
                        "result": None,
                        "error": None
                    })
                )
                
                # Run the analysis
                result = await analyze_ad_with_structured_workflow(
                    request.image_url, 
                    request.product_context
                )
                
                # Update task status
                await redis_client.set(
                    f"task:{task_id}", 
                    json.dumps({
                        "status": "completed",
                        "result": result,
                        "error": None
                    })
                )
                
            except Exception as e:
                # Log the error
                logger.error(f"Error in structured analysis task {task_id}: {str(e)}")
                
                # Update task status with error
                redis_client = await get_redis()
                await redis_client.set(
                    f"task:{task_id}", 
                    json.dumps({
                        "status": "failed",
                        "result": None,
                        "error": str(e)
                    })
                )
        
        # Add the task to background tasks
        background_tasks.add_task(run_analysis_task)
        
        # Return the task ID for polling
        return StructuredAnalysisResponse(
            task_id=task_id,
            status="processing"
        )
        
    except Exception as e:
        logger.error(f"Error initiating structured analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate structured analysis: {str(e)}")

@router.get("/structured-analysis/{task_id}", response_model=StructuredAnalysisResponse)
async def get_structured_analysis_result(task_id: str):
    """
    Get the result of a structured analysis task
    """
    try:
        # Get Redis client
        redis_client = await get_redis()
        
        # Get task data from Redis
        task_data_str = await redis_client.get(f"task:{task_id}")
        if not task_data_str:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        # Parse task data
        task_data = json.loads(task_data_str)
        
        # Return the task data
        return StructuredAnalysisResponse(
            task_id=task_id,
            status=task_data.get("status", "unknown"),
            result=task_data.get("result"),
            error=task_data.get("error")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving structured analysis result: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve structured analysis result: {str(e)}") 