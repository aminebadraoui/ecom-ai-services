import json
import asyncio
import nest_asyncio
from celery import Task
from pydantic_ai import Agent, ImageUrl, RunContext, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
import logging
from redis import Redis
from typing import Dict, Any

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.ad_concept import AdConceptOutput
from app.models.common import TaskResult
from app.tasks.ad_analysis_workflow import analyze_ad_with_structured_workflow

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Redis client
redis_client = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
    decode_responses=True
)

# Apply nest_asyncio to make asyncio play nice in Celery tasks
nest_asyncio.apply()

celery_app.conf.task_routes = {
    "app.tasks.ad_concept_tasks.*": {"queue": "ad-concept"},
    "app.tasks.sales_page_tasks.*": {"queue": "sales-page"},
}

celery_app.conf.update(
    task_track_started=True,
    result_expires=3600,  # 1 hour
    broker_connection_retry_on_startup=True,
)

class AdConceptTask(Task):
    """Base task for ad concept extraction"""
    
    def update_state(self, task_id, status, result=None, error=None):
        """Update task state in Redis"""
        task_data = TaskResult(
            status=status,
            result=result,
            error=error
        ).model_dump()
        redis_client.set(f"task:{task_id}", json.dumps(task_data))

@celery_app.task(base=AdConceptTask, bind=True, name="app.tasks.ad_concept_tasks.extract_ad_concept_with_context")
def extract_ad_concept_with_context(self, image_url: str, product_context: dict, task_id: str):
    """
    Process an ad concept extraction request with product context and store the result in Redis
    
    Args:
        image_url: URL of the image to analyze
        product_context: Product information from sales page analysis
        task_id: Unique ID for tracking the task
    """
    # Update task status to "processing"
    self.update_state(task_id, "processing")

    try:
        # Try the structured workflow approach
        loop = asyncio.get_event_loop()
        result_dict = loop.run_until_complete(analyze_ad_with_structured_workflow(image_url, product_context))
        
        # Store the successful result
        self.update_state(task_id, "completed", result=result_dict)
        return {"status": "completed", "task_id": task_id, "success": True}
    
    except Exception as e:
        logger.error(f"Error in structured workflow for task {task_id}: {str(e)}")
        
        # Simple fallback with direct agent
        try:
            async def process_with_agent():
                agent = Agent(
                    "openai:gpt-4o",
                    result_type=AdConceptOutput,
                    retries=3,
                    system_prompt=f"""You are an Ad Creative Analysis Agent analyzing an advertisement that will be applied to this product type:
{json.dumps(product_context, indent=2)}

Return a complete JSON structure with the following fields:
{{
  "title": "Brief title describing the ad",
  "summary": "Overview of the ad's approach",
  "details": {{
    "elements": [array of elements with {{type, position, purpose, styling, proportion}}],
    "visual_flow": "How the eye is guided",
    "visual_tone": "Emotional/psychological impact",
    "color_strategy": "Strategic use of color",
    "typography_approach": "Font choices and text presentation",
    "spacing_technique": "Use of whitespace",
    "engagement_mechanics": "How the ad maintains attention",
    "conversion_elements": "Call-to-action elements",
    "best_practices": [array of design principles],
    "primary_offering_visibility": {{"is_visible": boolean, "description": "How the offering stands out"}}
  }}
}}

Focus on transferable techniques rather than the specific product category."""
                )
                
                user_prompt = f"""Analyze this advertisement with knowledge it may be applied to this product type:
{json.dumps(product_context, indent=2)}

IMPORTANT: Focus on the TRANSFERABLE TECHNIQUES, not the specific product category.

Your response must be valid JSON with all required fields."""
                
                return await agent.run([user_prompt, ImageUrl(url=image_url)])
            
            # Run the fallback
            result = loop.run_until_complete(process_with_agent())
            
            # Parse the result based on its structure
            if hasattr(result, 'data'):
                output = result.data
            elif hasattr(result, 'message') and hasattr(result.message, 'content'):
                # Parse JSON from content if needed
                content = result.message.content
                parsed_data = json.loads(content)
                output = AdConceptOutput(
                    title=parsed_data.get("title", ""),
                    summary=parsed_data.get("summary", ""),
                    details=parsed_data.get("details", {})
                )
            else:
                output = result
            
            # Store result
            result_dict = output.model_dump()
            self.update_state(task_id, "completed", result=result_dict)
            return {"status": "completed", "task_id": task_id, "success": True, "used_fallback": True}
            
        except Exception as fallback_error:
            logger.error(f"Fallback also failed for task {task_id}: {str(fallback_error)}")
            self.update_state(task_id, "failed", error=str(e))
            return {"status": "failed", "task_id": task_id, "error": str(e), "success": False} 