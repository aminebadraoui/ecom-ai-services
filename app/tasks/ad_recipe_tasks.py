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
        # Step 1: Check if ad concept exists in Supabase
        ad_concept_data = supabase_service.get_ad_concept_by_archive_id(ad_archive_id)
        
        # If not found, generate a new one
        if not ad_concept_data:
            logger.info(f"No existing ad concept found for {ad_archive_id}, generating one...")
            
            # Generate a new subtask ID
            concept_task_id = f"{task_id}_concept"
            
            # Import at runtime to avoid circular import
            from app.tasks.ad_concept_tasks import extract_ad_concept
            
            # Run ad concept extraction task synchronously
            concept_result = extract_ad_concept(image_url, concept_task_id)
            
            # Check if the task was successful
            concept_task_data = json.loads(redis_client.get(f"task:{concept_task_id}"))
            if concept_task_data.get("status") != "completed":
                raise Exception(f"Failed to extract ad concept: {concept_task_data.get('error')}")
            
            # Get the ad concept result
            ad_concept_json = concept_task_data.get("result")
            
            # Store in Supabase
            # Ensure user_id is a valid UUID or handle as needed
            try:
                # Try to parse as UUID to validate
                from uuid import UUID
                UUID(user_id)
                supabase_service.store_ad_concept(ad_archive_id, image_url, ad_concept_json, user_id)
            except ValueError:
                # If user_id is not a valid UUID, generate a new one or use a default
                logger.warning(f"Invalid UUID format for user_id: {user_id}. Using a generated UUID instead.")
                import uuid
                valid_uuid = str(uuid.uuid4())
                supabase_service.store_ad_concept(ad_archive_id, image_url, ad_concept_json, valid_uuid)
        else:
            # Use existing ad concept data
            logger.info(f"Using existing ad concept for {ad_archive_id}")
            ad_concept_json = ad_concept_data.get("concept_json")
        
        # Step 2: Extract sales page information
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
        
        # Step 3: Generate the prompt template
        logger.info(f"Generating ad recipe prompt for {ad_archive_id}")
        
        # Format the prompt template
        recipe_prompt = f"""You are an expert ad creative designer. Create a high-converting Facebook ad using the provided information and assets:

### EXISTING AD CONCEPT (JSON):
This contains the visual layout, structure, and design approach to replicate.
{json.dumps(ad_concept_json, indent=2)}

### PRODUCT INFORMATION (JSON):
This contains the core product details to include in your ad.
{json.dumps(sales_page_json, indent=2)}

### USER-PROVIDED ASSETS:
You will receive:
- Product image(s)
- Brand logo
- Any additional visual assets the user provides

### CREATIVE REQUIREMENTS:

1. FORMAT:
   - Facebook Ad (9:16 vertical format)
   - Maintain standard Facebook ad margins and safe zones

2. LAYOUT & STRUCTURE:
   - Follow EXACTLY the layout structure described in the ad concept JSON
   - Pay special attention to visual hierarchy, element positioning, and flow
   - Maintain proportional sizing of elements as described

3. VISUAL IDENTITY:
   - Use ONLY the user-provided product images and logo
   - Maintain exact dimensions and proportions of product images and logo
   - Extract and use the brand color palette from the provided assets
   - Match typography style if possible, or use appropriate alternatives

4. PRIMARY OFFERING VISIBILITY:
   - Check the "primary_offering_visibility" field in the ad concept JSON
   - If "is_visible": true, prominently feature the product image as specified
   - If "is_visible": false, follow the conceptual approach without showing the product

5. MESSAGING:
   - Use the messaging tone and style from the ad concept JSON
   - Pull specific copy points from the product information JSON
   - Ensure all claims align with the product information provided
   - Include appropriate call-to-action as in the original concept

6. TECHNICAL SPECIFICATIONS:
   - Crisp, high-resolution output
   - Text must be legible on mobile screens
   - Properly layered file for easy editing
   - Follow Facebook's ad policies regarding text-to-image ratio

### PROCESS:
1. Analyze the ad concept JSON thoroughly to understand the visual approach
2. Extract key details from the product information JSON
3. Integrate the user-provided visual assets following the concept structure
4. Respect whether the product should be visible based on "primary_offering_visibility"
5. Generate a compelling ad that perfectly blends the concept structure with the provided assets

The final result should feel like a professional, high-converting ad that maintains the proven layout structure while perfectly showcasing the user's specific product and brand identity.
"""

        # Step 4: Store the complete recipe in Supabase
        # Ensure user_id is a valid UUID
        try:
            # Try to parse as UUID to validate
            from uuid import UUID
            UUID(user_id)
            supabase_service.store_ad_recipe(
                ad_archive_id=ad_archive_id,
                image_url=image_url,
                sales_url=sales_url,
                ad_concept_json=ad_concept_json,
                sales_page_json=sales_page_json,
                recipe_prompt=recipe_prompt,
                user_id=user_id
            )
        except ValueError:
            # If user_id is not a valid UUID, generate a new one or use a default
            logger.warning(f"Invalid UUID format for user_id: {user_id}. Using a generated UUID instead.")
            import uuid
            valid_uuid = str(uuid.uuid4())
            supabase_service.store_ad_recipe(
                ad_archive_id=ad_archive_id,
                image_url=image_url,
                sales_url=sales_url,
                ad_concept_json=ad_concept_json,
                sales_page_json=sales_page_json,
                recipe_prompt=recipe_prompt,
                user_id=valid_uuid
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