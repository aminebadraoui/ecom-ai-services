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
def generate_ad_recipe(self, ad_archive_id: str, image_url: str, sales_url: str, user_id: str, task_id: str):
    """
    Generate an ad recipe by combining ad concept and sales page data
    
    Args:
        ad_archive_id: ID of the ad in the archive
        image_url: URL of the image to analyze
        sales_url: URL of the sales page to analyze
        user_id: ID of the user making the request
        task_id: Unique ID for tracking the task
    """
    # Update task status to "processing"
    self.update_state(task_id, "processing")

    try:
        # Step 1: Extract sales page information first
        logger.info(f"Extracting sales page information for {sales_url}")
        sales_page_task_id = f"{task_id}_sales"
        
        # Import at runtime to avoid circular import
        from app.tasks.sales_page_tasks import extract_sales_page
        
        # Run sales page extraction task synchronously
        sales_result = extract_sales_page(sales_url, sales_page_task_id)
        
        # Check if the task was successful
        sales_task_data = json.loads(redis_client.get(f"task:{sales_page_task_id}"))
        if sales_task_data.get("status") != "completed":
            raise Exception(f"Failed to extract sales page data: {sales_task_data.get('error')}")
        
        # Get the sales page result
        sales_page_json = sales_task_data.get("result")
        
        # Step 2: Check if ad concept exists in Supabase
        ad_concept_data = supabase_service.get_ad_concept_by_archive_id(ad_archive_id)
        
        # If not found, generate a new one with product context
        if not ad_concept_data:
            logger.info(f"No existing ad concept found for {ad_archive_id}, generating one...")
            
            # Generate a new subtask ID
            concept_task_id = f"{task_id}_concept"
            
            # Import at runtime to avoid circular import
            from app.tasks.ad_concept_tasks import extract_ad_concept_with_context
            
            # Run ad concept extraction task synchronously with product context
            concept_result = extract_ad_concept_with_context(image_url, sales_page_json, concept_task_id)
            
            # Check if the task was successful
            concept_task_data = json.loads(redis_client.get(f"task:{concept_task_id}"))
            if concept_task_data.get("status") != "completed":
                raise Exception(f"Failed to extract ad concept: {concept_task_data.get('error')}")
            
            # Get the ad concept result
            ad_concept_json = concept_task_data.get("result")
            
            # Store in Supabase
            supabase_service.store_ad_concept(ad_archive_id, image_url, ad_concept_json, user_id)
        else:
            # Use existing ad concept data
            logger.info(f"Using existing ad concept for {ad_archive_id}")
            ad_concept_json = ad_concept_data.get("concept_json")
        
        # Step 3: Generate the prompt template
        logger.info(f"Generating ad recipe prompt for {ad_archive_id}")
        
        # Format the prompt template
        recipe_prompt = f"""You are an expert ad creative designer. Your task is to recreate a high-converting ad style for a new product by precisely following an existing ad's blueprint:

### AD BLUEPRINT (JSON):
This contains a comprehensive analysis of an existing successful ad's structure and approach. Follow this blueprint exactly.
{json.dumps(ad_concept_json, indent=2)}

### TARGET PRODUCT INFORMATION (JSON):
This contains the details of the specific product we want to advertise using the blueprint above.
{json.dumps(sales_page_json, indent=2)}

### USER-PROVIDED ASSETS:
The user will provide:
- Product image(s) that MUST be used exactly as provided
- Brand logo that MUST be used exactly as provided
- Any additional visual assets the user provides

### CREATIVE APPROACH:

1. EXACT RECREATION WITH NEW PRODUCT:
   - Your goal is to recreate the EXACT same ad approach/style from the blueprint, but featuring the user's product
   - Match every aspect of the layout, positioning, hierarchy, and flow described in the blueprint
   - Use the same visual techniques, proportions, and composition strategy
   - Maintain the same emotional tone and persuasive approach

2. VISUAL FIDELITY:
   - Follow all specific positioning details from the "elements" section of the blueprint
   - Recreate the exact same visual hierarchy and attention flow
   - Match the color strategy and typography approach as described
   - Replicate all engagement mechanics and conversion elements

3. PRODUCT REPRESENTATION:
   - Use ONLY the user-provided product images and logo
   - CRITICAL: Maintain the EXACT shape, form, and packaging of the product as shown in the provided image
   - Do NOT modify, stylize, or change the product shape in any way
   - Check the "primary_offering_visibility" in the blueprint to determine how prominently to feature the product

4. CONTENT ADAPTATION:
   - Take headlines, claims, benefits, and CTAs from the product information JSON
   - Adapt this content to fit the exact same structure and approach described in the blueprint 
   - Maintain the emotional tone and persuasive style from the blueprint
   - Ensure all claims align with the product information provided

5. TECHNICAL REQUIREMENTS:
   - Facebook Ad (9:16 vertical format)
   - Crisp, high-resolution output
   - Text must be legible on mobile screens
   - Follow Facebook's ad policies regarding text-to-image ratio

### GUIDANCE FOR MAXIMIZING EFFECTIVENESS:
1. First, thoroughly study the blueprint to understand exactly how the original ad was structured
2. Identify all key elements and their relationships to each other
3. Map out how to replace the original content with the target product content while maintaining the EXACT same structure
4. Pay special attention to how the blueprint describes:
   - Visual flow and attention direction
   - Emotional tone and persuasive techniques
   - Spacing, proportion, and hierarchy 
   - Conversion elements and calls to action

Your goal is to make an ad that looks like it was created by the same designer who made the original ad, following the exact same creative approach, but featuring the user's product instead.
"""

        # Step 4: Store the complete recipe in Supabase
        supabase_service.store_ad_recipe(
            ad_archive_id=ad_archive_id,
            image_url=image_url,
            sales_url=sales_url,
            ad_concept_json=ad_concept_json,
            sales_page_json=sales_page_json,
            recipe_prompt=recipe_prompt,
            user_id=user_id
        )
        
        # Step 5: Update task status to "completed"
        result_dict = {
            "ad_archive_id": ad_archive_id,
            "image_url": image_url,
            "sales_url": sales_url,
            "ad_concept_json": ad_concept_json,
            "sales_page_json": sales_page_json,
            "recipe_prompt": recipe_prompt,
            "user_id": user_id
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