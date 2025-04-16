import json
import asyncio
import nest_asyncio
from celery import Task
from pydantic_ai import Agent, ImageUrl, RunContext, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
import logging
from redis import Redis

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
                system_prompt="""You are analyzing a visual advertisement template structure.

Your task is to generate an extremely detailed and structured description of this ad template in JSON format. Focus solely on its layout, visual hierarchy, components, spacing, balance, and design approach. Explain how each element contributes to the overall effectiveness of the ad from a UX, marketing, and visual communication perspective.

IMPORTANT: You must NEVER reference specific product categories, niches, industries, or types of goods/services (e.g., skincare, fitness, tech, healthcare, fashion, etc.). Your analysis must be 100% product-category agnostic, focusing only on the visual concept, approach, and design patterns.

Describe each component in abstract terms that could apply to ANY type of advertisement (e.g., "primary visual element," "supporting text block," "contrast element," "call-to-action region").

REQUIRED OUTPUT STRUCTURE:
Your analysis must include these specific sections:
1. "title" - A descriptive name for this ad concept template that ONLY references the visual approach or structure (e.g., "Split-Screen Comparison Template", "Testimonial Showcase Layout", "Minimalist Feature Highlight")
2. "summary" - A brief 1-3 sentence description of the overall ad concept template and its visual approach WITHOUT mentioning any product category
3. "details" - A dictionary containing ALL other analysis points, including:
   a. "elements" - An array of objects describing each visual element in the image (position, purpose, styling)
      Each element should include:
      - "type": The type of element (e.g., primary_visual, headline, etc.)
      - "position": Where this element is positioned
      - "purpose": What purpose this element serves
      - "styling": The visual style of this element
   b. "visual_flow" - Description of how the viewer's attention moves through the image
   c. "visual_tone" - The overall tone/vibe of the image
   d. "best_practices" - List of visual design best practices demonstrated
   e. Any other observations, details, or analysis you find relevant
   f. "primary_offering_visibility" - Boolean and description indicating whether a primary offering/item is visibly shown in the ad (true/false plus brief explanation)

The output JSON MUST match this structure:
{
  "title": "Contrast-Based Focus Template",
  "summary": "A template that uses strong visual contrast and strategic positioning to draw attention to key elements and guide the viewer through a visual hierarchy.",
  "details": {
    "elements": [
      {
        "type": "primary_visual",
        "position": "center",
        "purpose": "To establish the main focal point",
        "styling": "High contrast against background, prominent sizing"
      },
      {
        "type": "headline",
        "position": "top section",
        "purpose": "To communicate the main message",
        "styling": "Bold typography with emphasis on key words"
      }
    ],
    "visual_flow": "The eye is first drawn to the central visual element, then to the headline above, followed by supporting elements...",
    "visual_tone": "Bold, confident, and direct with strong visual hierarchy...",
    "best_practices": [
      "Strategic use of negative space to create focus", 
      "Visual contrast to establish hierarchy"
    ],
    "color_palette": {
      "primary": "Deep contrast color",
      "secondary": "Neutral tone",
      "accent": "Attention-grabbing highlight"
    },
    "spacing_strategy": "Intentional clustering of related elements with breathing room between sections",
    "primary_offering_visibility": {
      "is_visible": true,
      "description": "The main offering is prominently displayed in the center of the composition as the focal point of the advertisement"
    }
  }
}

You should include as much relevant detail as possible in the "details" dictionary. Don't limit yourself to the examples above - add any additional analysis that would be valuable.

CRITICAL REMINDER: 
1. Your response MUST INCLUDE THE REQUIRED FIELDS:
   - "title" (required) - NO PRODUCT CATEGORIES MENTIONED
   - "summary" (required) - NO PRODUCT CATEGORIES MENTIONED
   - "details" dictionary (required) with all analysis information

2. NEVER mention any specific:
   - Product categories
   - Industries
   - Niches
   - Types of goods or services

The goal is to create a completely product-agnostic visual template analysis that focuses purely on design approach, visual strategy, and communication techniques.
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
                user_prompt = """Analyze this advertisement template and provide a detailed, structured description of its visual approach and design elements.

YOUR RESPONSE MUST INCLUDE ALL FIELDS SPECIFIED IN THE INSTRUCTIONS:
- "title" - A descriptive name for the ad concept template (NO product categories)
- "summary" - A brief description of the overall visual concept (NO product categories)
- "details" - A dictionary containing all your detailed analysis

IMPORTANT: Your analysis must be completely product-category agnostic. DO NOT mention any specific industries, niches, products, or services. Focus ONLY on the visual design approach, layout, and communication strategy.

The "details" dictionary should be as comprehensive as possible. Include elements, visual flow, tone, best practices, and any other observations about the visual design approach.

Be sure to include the "primary_offering_visibility" field in your details to indicate whether a main item/offering is visibly shown in the ad (true/false with explanation).

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