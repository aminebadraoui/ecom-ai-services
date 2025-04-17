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
                system_prompt="""You are analyzing an advertisement to create a detailed blueprint that can be applied to different products.

Your task is to generate an extremely detailed, structured description of this ad in JSON format. Capture all elements of its layout, visual hierarchy, components, spacing, balance, and design technique. Explain how each element contributes to the overall effectiveness from marketing, UX, and visual communication perspectives.

IMPORTANT: While analyzing the ad, do NOT focus on the specific product category (e.g., skincare, fitness, tech). Instead, document the APPROACH, TECHNIQUES, and STRUCTURE in a way that can be transferred to any product. Focus on HOW the ad works rather than WHAT it's selling.

Describe each component in detail including:
- Exact positioning (relative to other elements)
- Size proportions 
- Color relationships
- Typography style and hierarchy
- Visual treatments (shadows, gradients, borders)
- Negative space usage
- Focal points and attention flow

REQUIRED OUTPUT STRUCTURE:
Your analysis must include these specific sections:
1. "title" - A descriptive name for this ad concept template that references the visual approach or structure
2. "summary" - A brief 1-3 sentence description of the overall ad concept and its visual approach
3. "details" - A dictionary containing ALL other analysis points, including:
   a. "elements" - An array of objects describing each visual element in the image with maximum detail:
      Each element should include:
      - "type": The type of element (e.g., primary_visual, headline, etc.)
      - "position": Precise positioning description
      - "purpose": Detailed description of its functional purpose
      - "styling": Comprehensive styling details including fonts, colors, treatments
      - "proportion": Approximate size relative to the overall ad
   b. "visual_flow" - Step-by-step description of how the viewer's attention moves through the ad
   c. "visual_tone" - Comprehensive analysis of the mood, tone, and emotional qualities
   d. "color_strategy" - Detailed analysis of color usage, relationships, and psychology
   e. "typography_approach" - Analysis of font choices, sizing patterns, and text styling
   f. "spacing_technique" - How spacing and alignment are used strategically
   g. "engagement_mechanics" - Techniques used to grab and maintain attention
   h. "conversion_elements" - Features designed to drive action
   i. "best_practices" - List of effective design and marketing techniques demonstrated
   j. "primary_offering_visibility" - Whether and how the main offering is shown in the ad

The output JSON MUST match this structure, but include as much detail as possible within each section.

REMEMBER: The goal is to create a transferable blueprint that captures EVERY aspect of what makes this ad effective, without being tied to the specific product being advertised.
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
                
                # Check that details are present and not empty
                if not hasattr(result, "details") or not result.details:
                    logger.error(f"Missing details dictionary in result for task {task_id}")
                    raise ModelRetry("Your response is missing the 'details' dictionary or it's empty. This field is required and must contain comprehensive analysis.")
                
                # Check for elements in details
                if "elements" not in result.details or not result.details["elements"]:
                    logger.warning(f"No elements found in details for task {task_id}")
                    raise ModelRetry("Your response must include an 'elements' array in the details dictionary with comprehensive element analysis.")
                
                # Check for other required fields in details
                required_detail_fields = ["visual_flow", "visual_tone", "color_strategy", "typography_approach", 
                                         "spacing_technique", "engagement_mechanics", "conversion_elements", 
                                         "best_practices", "primary_offering_visibility"]
                
                missing_fields = [field for field in required_detail_fields if field not in result.details]
                if missing_fields:
                    logger.error(f"Missing required detail fields: {', '.join(missing_fields)}")
                    raise ModelRetry(f"Your response is missing these required fields in the details dictionary: {', '.join(missing_fields)}. Please include all required fields with comprehensive analysis.")
                
                logger.info(f"Result validation successful for task {task_id}")
                return result
            
            try:
                # Process the image with the agent
                logger.info(f"Starting agent run for task {task_id}")
                
                # Build the prompt
                user_prompt = """Analyze this advertisement and provide an extremely detailed, structured breakdown of its approach, layout, and techniques.

YOUR RESPONSE MUST INCLUDE ALL FIELDS SPECIFIED IN THE INSTRUCTIONS and be as detailed as possible within each section.

CRITICAL: The 'details' dictionary is the most important part of your analysis and must be COMPREHENSIVE. Include ALL required fields with thorough descriptions. This blueprint will be used directly to recreate ads, so missing details will result in incomplete recreations.

IMPORTANT:
1. Focus on the AD STRUCTURE and TECHNIQUES, not the specific product category
2. Document precisely how elements are positioned, sized, and relate to each other
3. Analyze the visual hierarchy, attention flow, and marketing psychology
4. Note specific details about typography, color usage, and spacing
5. Identify all persuasive and conversion elements

Don't omit ANY visual or structural details - the goal is to create a comprehensive blueprint that could be used to recreate the same advertising approach for an entirely different product.

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
        # Define a fully self-contained async function
        async def process_with_agent():
            # Create a new agent for this task
            agent = Agent(
                "openai:gpt-4o",
                result_type=AdConceptOutput,
                retries=5,  # Set retries for validation
                system_prompt=f"""You are analyzing an advertisement to create a detailed blueprint that can be applied to a specific product.

Your task is to generate an extremely detailed, structured description of this ad in JSON format. Capture all elements of its layout, visual hierarchy, components, spacing, balance, and design technique. Explain how each element contributes to the overall effectiveness from marketing, UX, and visual communication perspectives.

IMPORTANT: You are analyzing this ad with knowledge of the target product it will be applied to:
{json.dumps(product_context, indent=2)}

Focus on documenting the APPROACH, TECHNIQUES, and STRUCTURE in a way that could be effectively applied to this specific product. Use terminology relevant to the product type when describing elements (e.g., if the target product is patches, describe floating elements as "patches" not "capsules").

Describe each component in detail including:
- Exact positioning (relative to other elements)
- Size proportions 
- Color relationships
- Typography style and hierarchy
- Visual treatments (shadows, gradients, borders)
- Negative space usage
- Focal points and attention flow

REQUIRED OUTPUT STRUCTURE:
Your analysis must include these specific sections:
1. "title" - A descriptive name for this ad concept template that references the visual approach or structure (make it relevant to the target product type)
2. "summary" - A brief 1-3 sentence description of the overall ad concept and its visual approach
3. "details" - A dictionary containing ALL other analysis points, including:
   a. "elements" - An array of objects describing each visual element in the image with maximum detail:
      Each element should include:
      - "type": The type of element (e.g., primary_visual, headline, etc.)
      - "position": Precise positioning description
      - "purpose": Detailed description of its functional purpose
      - "styling": Comprehensive styling details including fonts, colors, treatments
      - "proportion": Approximate size relative to the overall ad
   b. "visual_flow" - Step-by-step description of how the viewer's attention moves through the ad
   c. "visual_tone" - Comprehensive analysis of the mood, tone, and emotional qualities
   d. "color_strategy" - Detailed analysis of color usage, relationships, and psychology
   e. "typography_approach" - Analysis of font choices, sizing patterns, and text styling
   f. "spacing_technique" - How spacing and alignment are used strategically
   g. "engagement_mechanics" - Techniques used to grab and maintain attention
   h. "conversion_elements" - Features designed to drive action
   i. "best_practices" - List of effective design and marketing techniques demonstrated
   j. "primary_offering_visibility" - Whether and how the main offering is shown in the ad

The output JSON MUST match this structure, but include as much detail as possible within each section.

REMEMBER: Use terminology consistent with the product type from the context. The goal is to create a transferable blueprint that captures EVERY aspect of what makes this ad effective, but with relevant language for the target product.
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
                
                # Check that details are present and not empty
                if not hasattr(result, "details") or not result.details:
                    logger.error(f"Missing details dictionary in result for task {task_id}")
                    raise ModelRetry("Your response is missing the 'details' dictionary or it's empty. This field is required and must contain comprehensive analysis.")
                
                # Check for elements in details
                if "elements" not in result.details or not result.details["elements"]:
                    logger.warning(f"No elements found in details for task {task_id}")
                    raise ModelRetry("Your response must include an 'elements' array in the details dictionary with comprehensive element analysis.")
                
                # Check for other required fields in details
                required_detail_fields = ["visual_flow", "visual_tone", "color_strategy", "typography_approach", 
                                         "spacing_technique", "engagement_mechanics", "conversion_elements", 
                                         "best_practices", "primary_offering_visibility"]
                
                missing_fields = [field for field in required_detail_fields if field not in result.details]
                if missing_fields:
                    logger.error(f"Missing required detail fields: {', '.join(missing_fields)}")
                    raise ModelRetry(f"Your response is missing these required fields in the details dictionary: {', '.join(missing_fields)}. Please include all required fields with comprehensive analysis.")
                
                logger.info(f"Result validation successful for task {task_id}")
                return result
            
            try:
                # Process the image with the agent
                logger.info(f"Starting agent run for task {task_id}")
                
                # Build the prompt
                user_prompt = f"""Analyze this advertisement and provide an extremely detailed, structured breakdown of its approach, layout, and techniques.

You have been provided with details about the product type this analysis will be applied to:
{json.dumps(product_context, indent=2)}

YOUR RESPONSE MUST INCLUDE ALL FIELDS SPECIFIED IN THE INSTRUCTIONS and be as detailed as possible within each section.

CRITICAL: The 'details' dictionary is the most important part of your analysis and must be COMPREHENSIVE. Include ALL required fields with thorough descriptions. This blueprint will be used directly to recreate ads, so missing details will result in incomplete recreations.

IMPORTANT:
1. Use terminology that matches the product type (e.g., if analyzing floating elements for a patch product, describe them as "floating patches" not "floating capsules")
2. Document precisely how elements are positioned, sized, and relate to each other
3. Analyze the visual hierarchy, attention flow, and marketing psychology
4. Note specific details about typography, color usage, and spacing
5. Identify all persuasive and conversion elements

The goal is to create a comprehensive blueprint that effectively captures this ad's structure and can be directly applied to the specified product type.

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