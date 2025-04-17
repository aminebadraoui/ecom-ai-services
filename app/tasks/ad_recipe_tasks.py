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
    logger.info(f"Starting ad recipe generation task {task_id} for ad_archive_id={ad_archive_id}")

    try:
        # Step 1: Extract sales page information
        logger.info(f"Step 1: Extracting sales page information for {sales_url}")
        sales_page_task_id = f"{task_id}_sales"
        
        # Import at runtime to avoid circular import
        from app.tasks.sales_page_tasks import extract_sales_page
        
        # Run sales page extraction task synchronously
        sales_result = extract_sales_page(sales_url, sales_page_task_id)
        
        # Check if the task was successful
        sales_task_data = json.loads(redis_client.get(f"task:{sales_page_task_id}"))
        if sales_task_data.get("status") != "completed":
            logger.error(f"Failed to extract sales page data: {sales_task_data.get('error')}")
            raise Exception(f"Failed to extract sales page data: {sales_task_data.get('error')}")
        
        # Get the sales page result
        sales_page_json = sales_task_data.get("result")
        logger.info(f"Successfully extracted sales page data with {len(sales_page_json) if isinstance(sales_page_json, dict) else 0} fields")
        
        # Step 2: Check if we need to generate a new ad concept
        logger.info(f"Step 2: Checking if ad concept exists for {ad_archive_id}")
        ad_concept_data = supabase_service.get_ad_concept_by_archive_id(ad_archive_id)
        should_generate_new_concept = False
        
        # Simplified validation of existing ad concept
        if ad_concept_data:
            ad_concept_json = ad_concept_data.get("concept_json", {})
            logger.info(f"Found existing ad concept for {ad_archive_id}")
            
            # Quick validation of required structure
            if (not isinstance(ad_concept_json, dict) or 
                not ad_concept_json.get("details") or 
                not isinstance(ad_concept_json.get("details"), dict) or
                not ad_concept_json.get("details", {}).get("elements")):
                
                logger.warning(f"Existing ad concept has invalid structure, will generate new one")
                should_generate_new_concept = True
            else:
                logger.info(f"Using existing valid ad concept for {ad_archive_id}")
        else:
            logger.info(f"No existing ad concept found for {ad_archive_id}")
            should_generate_new_concept = True
        
        # Generate a new concept if needed using the structured workflow
        if should_generate_new_concept:
            logger.info(f"Generating new ad concept using structured workflow")
            
            # Create an event loop for the async workflow
            loop = asyncio.get_event_loop()
            
            # Run the structured workflow analysis
            logger.info(f"Starting structured ad analysis workflow for {image_url}")
            ad_concept_json = loop.run_until_complete(
                analyze_ad_with_structured_workflow(image_url, sales_page_json)
            )
            
            logger.info(f"Successfully generated ad concept with structured workflow")
            logger.info(f"Ad concept title: '{ad_concept_json.get('title')}'")
            logger.info(f"Ad concept elements: {len(ad_concept_json.get('details', {}).get('elements', []))} elements found")
            
            # Store the new concept in Supabase
            logger.info(f"Storing new ad concept in Supabase")
            supabase_service.store_ad_concept(ad_archive_id, image_url, ad_concept_json, user_id)
        
        # Step 3: Generate the recipe prompt
        logger.info(f"Step 3: Generating ad recipe prompt")
        
        # Final validation before generating the recipe
        if not isinstance(ad_concept_json, dict):
            logger.error("Ad concept is not a dictionary")
            raise Exception("Ad concept has invalid format")
            
        if not ad_concept_json.get("details"):
            logger.error("Ad concept is missing details dictionary")
            raise Exception("Ad concept is missing details dictionary")
            
        if not isinstance(ad_concept_json["details"], dict):
            logger.error("Ad concept details is not a dictionary")
            raise Exception("Ad concept details has invalid format")
            
        if "elements" not in ad_concept_json["details"]:
            logger.error("Ad concept details is missing elements array")
            raise Exception("Ad concept details is missing elements array")
            
        # Log details about the ad concept
        elements = ad_concept_json["details"].get("elements", [])
        logger.info(f"Ad concept contains {len(elements)} elements")
        
        for i, element in enumerate(elements):
            logger.info(f"Element {i+1}: {element.get('type')} - {element.get('position')}")
            
        logger.info(f"Ad concept visual flow: {ad_concept_json['details'].get('visual_flow', '')[:50]}...")
        logger.info(f"Ad concept color strategy: {ad_concept_json['details'].get('color_strategy', '')[:50]}...")
            
        # Format the prompt template
        logger.info(f"Creating recipe prompt template")
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
        logger.info(f"Recipe prompt created, length: {len(recipe_prompt)} characters")

        # Step 4: Store the complete recipe in Supabase
        logger.info(f"Step 4: Storing ad recipe in Supabase")
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
        logger.info(f"Step 5: Completing task {task_id}")
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
        
        logger.info(f"Ad recipe generation task {task_id} completed successfully")
        
        # Return a simple dict
        return {
            "status": "completed", 
            "task_id": task_id,
            "success": True
        }
    
    except Exception as e:
        # Store the error
        import traceback
        error_details = f"{str(e)}\n{traceback.format_exc()}"
        logger.error(f"Error in ad recipe generation task {task_id}: {error_details}")
        
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