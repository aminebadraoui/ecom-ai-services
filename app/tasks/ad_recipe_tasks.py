import json
import asyncio
import nest_asyncio
from celery import Task
from pydantic_ai import Agent, ImageUrl
from pydantic_ai.exceptions import UnexpectedModelBehavior
import logging
from redis import Redis

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.common import TaskResult
from app.models.ad_concept import AdConceptOutput
from app.models.sales_page import SalesPageOutput
from app.services.supabase_service import supabase_service
from app.tasks.ad_concept_tasks import extract_ad_concept
from app.tasks.sales_page_tasks import extract_sales_page

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
    "app.tasks.ad_recipe_tasks.*": {"queue": "ad-recipe"},
}

class AdRecipeTask(Task):
    """Base task for ad recipe generation"""
    
    def update_state(self, task_id, status, result=None, error=None):
        """Update task state in Redis"""
        task_data = TaskResult(
            status=status,
            result=result,
            error=error
        ).model_dump()
        redis_client.set(f"task:{task_id}", json.dumps(task_data))

@celery_app.task(base=AdRecipeTask, bind=True, name="app.tasks.ad_recipe_tasks.generate_ad_recipe")
def generate_ad_recipe(self, ad_archive_id: str, image_url: str, sales_url: str, task_id: str):
    """
    Generate an ad recipe by combining ad concept and sales page data
    
    Args:
        ad_archive_id: ID of the ad in the archive
        image_url: URL of the image to analyze
        sales_url: URL of the sales page to analyze
        task_id: Unique ID for tracking the task
    """
    # Update task status to "processing"
    self.update_state(task_id, "processing")

    try:
        # Step 1: Check if ad concept exists in Supabase
        ad_concept_data = supabase_service.get_ad_concept_by_archive_id(ad_archive_id)
        
        # If not found, generate a new one
        if not ad_concept_data:
            logger.info(f"No existing ad concept found for {ad_archive_id}, generating one...")
            
            # Generate a new subtask ID
            concept_task_id = f"{task_id}_concept"
            
            # Run ad concept extraction task synchronously
            concept_result = extract_ad_concept(image_url, concept_task_id)
            
            # Check if the task was successful
            concept_task_data = json.loads(redis_client.get(f"task:{concept_task_id}"))
            if concept_task_data.get("status") != "completed":
                raise Exception(f"Failed to extract ad concept: {concept_task_data.get('error')}")
            
            # Get the ad concept result
            ad_concept_json = concept_task_data.get("result")
            
            # Store in Supabase
            supabase_service.store_ad_concept(ad_archive_id, image_url, ad_concept_json)
        else:
            # Use existing ad concept data
            logger.info(f"Using existing ad concept for {ad_archive_id}")
            ad_concept_json = ad_concept_data.get("concept_json")
        
        # Step 2: Extract sales page information
        logger.info(f"Extracting sales page information for {sales_url}")
        sales_page_task_id = f"{task_id}_sales"
        
        # Run sales page extraction task synchronously
        sales_result = extract_sales_page(sales_url, sales_page_task_id)
        
        # Check if the task was successful
        sales_task_data = json.loads(redis_client.get(f"task:{sales_page_task_id}"))
        if sales_task_data.get("status") != "completed":
            raise Exception(f"Failed to extract sales page data: {sales_task_data.get('error')}")
        
        # Get the sales page result
        sales_page_json = sales_task_data.get("result")
        
        # Step 3: Generate the prompt template
        logger.info(f"Generating ad recipe prompt for {ad_archive_id}")
        
        # Format the prompt template
        recipe_prompt = f"""You are an expert ad creative designer. Use the following inputs to generate a high-converting Facebook ad image (9:16 format):
Existing Ad Description (JSON):
 This contains the layout, concept, structure, and messaging tone. Recreate the same concept and layout style as described here.
{json.dumps(ad_concept_json, indent=2)}

Product Info (JSON):
 Use only to reinforce or clarify the messaging in the ad description, if needed.

{json.dumps(sales_page_json, indent=2)}

Product Mockup (IMAGE):
 This is the sole source for all visual branding, including:

Logo
Colors
Fonts (style, if extractable)
Product image

Creative Instructions:
Format: Facebook Ad (9:16 square)
Design: Follow layout, concept, and ad structure from the ad description JSON.
Visuals: Extract all branding, colors, and product images only from the mockup.
Messaging: Use core messaging and tone from the ad JSON. You may refer to the product info JSON for support, but it should not define the structure or design.
CTA: Include if part of the original concept.
Design Quality: Bold, scroll-stopping, mobile-optimized, and visually clean.

Goal:
 Reimagine the ad concept described in the JSON using the branding and visual identity from the product mockup — resulting in a compelling, brand-aligned Facebook ad creative that maintains proven layout structure and messaging effectiveness.
Output: A Facebook-ready image ad (9:16 format).
"""

        # Step 4: Store the complete recipe in Supabase
        supabase_service.store_ad_recipe(
            ad_archive_id=ad_archive_id,
            image_url=image_url,
            sales_url=sales_url,
            ad_concept_json=ad_concept_json,
            sales_page_json=sales_page_json,
            recipe_prompt=recipe_prompt
        )
        
        # Step 5: Update task status to "completed"
        result_dict = {
            "ad_archive_id": ad_archive_id,
            "image_url": image_url,
            "sales_url": sales_url,
            "ad_concept_json": ad_concept_json,
            "sales_page_json": sales_page_json,
            "recipe_prompt": recipe_prompt
        }
        
        self.update_state(
            task_id, 
            "completed", 
            result=result_dict
        )
        
        # Return a simple dict, not the complex result object
        return {
            "status": "completed", 
            "task_id": task_id,
            "success": True
        }
    
    except Exception as e:
        # Store the error
        import traceback
        error_details = f"{str(e)}\n{traceback.format_exc()}"
        logger.error(f"Error processing task {task_id}: {error_details}")
        
        self.update_state(
            task_id, 
            "failed", 
            error=str(e)
        )
        
        # Return a simple dict
        return {
            "status": "failed", 
            "task_id": task_id, 
            "error": str(e),
            "success": False
        } 