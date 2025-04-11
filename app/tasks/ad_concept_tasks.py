import json
import asyncio
import nest_asyncio
from celery import Task
from pydantic_ai import Agent, ImageUrl, RunContext, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from redis import Redis
import os
import logging

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.ad_concept import AdConceptOutput
from app.models.common import TaskResult

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Redis client
redis_client = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    decode_responses=True
)

# For debugging
print(f"Connecting to Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}")

# Apply nest_asyncio to make asyncio play nice in Celery tasks
nest_asyncio.apply()

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

@celery_app.task(base=AdConceptTask, bind=True, name="app.tasks.ad_concept_tasks.extract_ad_concept")
def extract_ad_concept(self, image_url: str, task_id: str):
    """
    Process an ad concept extraction request and store the result in Redis
    
    Args:
        image_url: URL of the image to analyze
        task_id: Unique ID for tracking the task
    """
    # Update task status to "processing"
    self.update_state(task_id, "processing")

    try:
        # Define a fully self-contained async function
        async def process_with_agent():
            # Create a new agent for this task
            agent = Agent(
                "openai:gpt-4o",
                result_type=AdConceptOutput,
                retries=5,  # Set retries for validation
                system_prompt="""You are analyzing a product image intended for use on a product detail page.

Your task is to generate an extremely detailed and structured description of this image in JSON format. Focus on its layout, visual hierarchy, components, spacing, balance, and design purpose. Explain how each element contributes to the overall effectiveness of the image from a UX, marketing, and visual communication perspective.

Do not reference specific product details or branding (e.g., names, logos, text, or images unique to a particular product). Instead, abstract each component into a reusable format that could be applied to any type of product (e.g., "product photo area," "badge for product feature," "call-to-action button").

REQUIRED OUTPUT STRUCTURE:
Your analysis must include these specific sections:
1. "title" - A descriptive name for this ad concept template (e.g., "Premium Product Showcase", "Feature-Focused Display")
2. "summary" - A brief 1-3 sentence description of the overall ad concept template and its main purpose
3. "details" - A dictionary containing ALL other analysis points, including:
   a. "elements" - An array of objects describing each visual element in the image (position, purpose, styling)
      Each element should include:
      - "type": The type of element (e.g., product_photo, headline, etc.)
      - "position": Where this element is positioned
      - "purpose": What purpose this element serves
      - "styling": The visual style of this element
   b. "visual_flow" - Description of how the viewer's attention moves through the image
   c. "visual_tone" - The overall tone/vibe of the image
   d. "best_practices" - List of persuasive product imagery best practices
   e. Any other observations, details, or analysis you find relevant

The output JSON MUST match this structure:
{
  "title": "Premium Product Showcase Template",
  "summary": "A minimalist product display template that emphasizes high-end positioning through clean aesthetics and strategic negative space.",
  "details": {
    "elements": [
      {
        "type": "product_photo",
        "position": "center",
        "purpose": "To showcase the product's appearance",
        "styling": "Clean, isolated against white background"
      },
      {
        "type": "product_name",
        "position": "below product",
        "purpose": "To identify the product",
        "styling": "Bold, larger font"
      }
    ],
    "visual_flow": "The eye is first drawn to the product in the center, then to supporting elements...",
    "visual_tone": "Clean, professional, and premium with subdued colors...",
    "best_practices": [
      "Strong focal point with the product as hero", 
      "High-quality photography with consistent lighting"
    ],
    "color_palette": {
      "primary": "White",
      "secondary": "Light gray",
      "accent": "Brand color"
    },
    "spacing_strategy": "Generous negative space creates premium feel"
  }
}

You should include as much relevant detail as possible in the "details" dictionary. Don't limit yourself to the examples above - add any additional analysis that would be valuable.

CRITICAL REMINDER: Your response MUST INCLUDE THE REQUIRED FIELDS:
- "title" (required)
- "summary" (required)
- "details" dictionary (required) with all analysis information

The goal is to create a modular visual template that communicates value, builds trust, and encourages engagement—regardless of the product type.
"""
            )
            
            # Add a result validator to ensure the output has the correct structure
            @agent.result_validator
            async def validate_ad_concept(ctx: RunContext, result: AdConceptOutput) -> AdConceptOutput:
                """Validate that the AdConceptOutput has the correct structure with flexible details."""
                logger.info(f"Validating result structure for task {task_id}...")
                
                # Check that we have the required fields
                if not result.title or not result.summary:
                    logger.error(f"Missing required fields in result for task {task_id}")
                    raise ModelRetry("Your response is missing required fields. Please include both 'title' and 'summary' fields.")
                
                # Check that details are present
                if not hasattr(result, "details") or not result.details:
                    logger.error(f"Missing details dictionary in result for task {task_id}")
                    result.details = {}
                
                # Check for elements in details
                if "elements" not in result.details or not result.details["elements"]:
                    logger.warning(f"No elements found in details for task {task_id}")
                    # Not making this required, but adding the warning to logs
                
                logger.info(f"Result validation successful for task {task_id}")
                return result
            
            try:
                # Process the image with the agent
                logger.info(f"Starting agent run for task {task_id}")
                
                # Build the prompt
                user_prompt = """Analyze this product image and provide a detailed structured description. 
                
YOUR RESPONSE MUST INCLUDE ALL FIELDS SPECIFIED IN THE INSTRUCTIONS:
- "title" - A descriptive name for the ad concept template
- "summary" - A brief description of the overall concept
- "details" - A dictionary containing all your detailed analysis

The "details" dictionary should be as comprehensive as possible. Include elements, visual flow, tone, best practices, and any other observations you can make about the image.

Follow the JSON structure exactly as requested."""
                
                # Run the agent with the image
                result = await agent.run([user_prompt, ImageUrl(url=image_url)])
                
                # Log the result
                logger.info(f"Final result data ({task_id}): {json.dumps(result.data.model_dump(), indent=2)}")
                return result.data
                
            except UnexpectedModelBehavior as model_error:
                # Log the error
                logger.error(f"Model behavior error for task {task_id}: {str(model_error)}")
                
                # Capture the cause if available
                if model_error.__cause__:
                    logger.error(f"Cause: {str(model_error.__cause__)}")
                
                raise model_error
        
        # Create a new event loop and run the async function
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(process_with_agent())
        
        # Store the successful result
        result_dict = result.model_dump()
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
        
        # Log additional information from the context if available
        if hasattr(e, 'context'):
            logger.error(f"Error context: {json.dumps(e.context, indent=2)}")
        
        # For validation errors, handle with pydantic_ai specific error handling
        if isinstance(e, UnexpectedModelBehavior):
            logger.error(f"Model behavior error: {str(e)}")
            # Additional details may be available in the cause
            if e.__cause__:
                logger.error(f"Cause: {str(e.__cause__)}")
        
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