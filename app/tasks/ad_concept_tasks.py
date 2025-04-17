import json
import asyncio
import nest_asyncio
from celery import Task
from pydantic_ai import Agent, ImageUrl, RunContext, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
import logging
from redis import Redis
from typing import Dict, Optional, Any
import re
import json as json_lib

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

FOCUS on describing:
- Exact positioning of elements
- Size proportions 
- Color relationships
- Typography style and hierarchy
- Visual treatments
- Negative space usage
- Focal points and attention flow

Your role is to provide complete, structured analysis in the exact JSON format required."""
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
                user_prompt = """Analyze this advertisement image and create a detailed, structured breakdown of its approach, layout and techniques.

Make sure the response is in valid JSON with this structure exactly:
{
  "title": "",
  "summary": "",
  "details": {
    "elements": [
      {
        "type": "",
        "position": "",
        "purpose": "", 
        "styling": "",
        "proportion": ""
      }
    ],
    "visual_flow": "",
    "visual_tone": "",
    "color_strategy": "", 
    "typography_approach": "",
    "spacing_technique": "",
    "engagement_mechanics": "",
    "conversion_elements": "",
    "best_practices": [],
    "primary_offering_visibility": {
      "is_visible": null,
      "description": ""
    }
  }
}

You MUST include the "details" dictionary with ALL fields shown above. This is the most critical part of the analysis.

The "details" object is ABSOLUTELY REQUIRED and must document:
1. ALL visual and text elements, their exact positioning, sizing, and relationships
2. The complete visual structure and hierarchy
3. How information is presented to guide the viewer's attention

Your response MUST be complete, detailed and STRICTLY follow the JSON structure shown above.
"""
                
                # Run the agent with the image
                result = await agent.run([user_prompt, ImageUrl(url=image_url)])
                
                # Validate and ensure the model produced a proper JSON structure
                result = ensure_valid_details_structure(result)
                
                return result
                
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

Your task is to generate an extremely detailed, structured description of this ad in JSON format. Capture all elements of its layout, visual hierarchy, components, spacing, balance, and design technique.

YOU MUST ALWAYS RETURN A COMPLETE JSON STRUCTURE with all required fields.

You are analyzing this ad with knowledge of the target product it will be applied to:
{json.dumps(product_context, indent=2)}

The output JSON MUST have this structure:
{{
  "title": "",
  "summary": "",
  "details": {{
    "elements": [
      {{
        "type": "",
        "position": "",
        "purpose": "",
        "styling": "",
        "proportion": ""
      }}
    ],
    "visual_flow": "",
    "visual_tone": "",
    "color_strategy": "",
    "typography_approach": "",
    "spacing_technique": "",
    "engagement_mechanics": "",
    "conversion_elements": "",
    "best_practices": [],
    "primary_offering_visibility": {{
      "is_visible": null,
      "description": ""
    }}
  }}
}}

You MUST include the "details" dictionary with ALL fields shown above.

Use terminology relevant to the product type when describing elements (e.g., if the target product is patches, describe floating elements as "patches" not "capsules").

FOCUS on describing:
- Exact positioning of elements
- Size proportions 
- Color relationships
- Typography style and hierarchy
- Visual treatments
- Negative space usage
- Focal points and attention flow

Your role is to provide complete, structured analysis in the exact JSON format required."""
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

YOUR RESPONSE MUST BE IN VALID JSON FORMAT with the following structure:
{{
  "title": "",
  "summary": "",
  "details": {{
    "elements": [
      {{
        "type": "",
        "position": "",
        "purpose": "",
        "styling": "",
        "proportion": ""
      }}
    ],
    "visual_flow": "",
    "visual_tone": "",
    "color_strategy": "",
    "typography_approach": "",
    "spacing_technique": "",
    "engagement_mechanics": "",
    "conversion_elements": "",
    "best_practices": [],
    "primary_offering_visibility": {{
      "is_visible": null,
      "description": ""
    }}
  }}
}}

IMPORTANT:
1. Include ALL the fields shown above exactly as structured
2. Ensure the "details" object is complete with all required sub-fields
3. Use terminology that matches the product type 
4. Focus on the ad structure and techniques, not specific product category
5. Document precisely how elements are positioned, sized, and relate to each other

You MUST provide the response in the JSON format above with all fields completed."""
                
                # Run the agent with the image
                result = await agent.run([user_prompt, ImageUrl(url=image_url)])
                
                # Validate and ensure the model produced a proper JSON structure
                result = ensure_valid_details_structure(result)
                
                return result
                
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

def ensure_valid_details_structure(result: Any) -> AdConceptOutput:
    """Ensures that the result contains a valid details structure.
    If structure is missing or malformed, attempts to fix it."""
    
    logger.info(f"Validating and ensuring valid details structure. Result type: {type(result)}")
    
    # If the result is already valid, return it
    if hasattr(result, 'details') and isinstance(result.details, dict) and result.details.get('elements'):
        required_fields = ["elements", "visual_flow", "visual_tone", "color_strategy", 
                         "typography_approach", "spacing_technique", "engagement_mechanics", 
                         "conversion_elements", "best_practices", "primary_offering_visibility"]
        
        missing_fields = [field for field in required_fields if field not in result.details]
        if not missing_fields:
            logger.info("Result already has valid details structure")
            return result
    
    # If we got here, we need to fix the result
    logger.warning("Result has missing or invalid details structure, attempting to fix")
    
    # Try to extract JSON structure from raw data if we have it
    raw_data = None
    if hasattr(result, 'raw_data') and result.raw_data:
        raw_data = result.raw_data
    elif hasattr(result, '_raw_response') and result._raw_response:
        raw_data = result._raw_response
    
    if raw_data:
        logger.info(f"Attempting to extract JSON from raw data: {raw_data[:200]}...")
        
        # Try to find and parse a JSON structure within the raw text
        json_pattern = r'({[\s\S]*})'
        match = re.search(json_pattern, raw_data)
        if match:
            try:
                extracted_json = match.group(1)
                parsed_json = json_lib.loads(extracted_json)
                logger.info(f"Successfully extracted JSON from raw text")
                
                # Update result with the extracted data
                if not hasattr(result, 'details') or not result.details:
                    if 'details' in parsed_json:
                        result.details = parsed_json['details']
                    else:
                        # Create minimal details structure from extracted data
                        result.details = create_details_from_partial(parsed_json)
                        
                if not hasattr(result, 'title') or not result.title:
                    result.title = parsed_json.get('title', "Ad Concept Analysis")
                    
                if not hasattr(result, 'summary') or not result.summary:
                    result.summary = parsed_json.get('summary', "Analysis of the advertisement visual structure")
                
                return result
            except Exception as e:
                logger.error(f"Failed to parse JSON from raw text: {str(e)}")
    
    # If we couldn't fix it, raise an exception
    logger.error("Failed to repair result structure")
    raise ValueError("Could not generate or repair a valid details structure")

def create_details_from_partial(data: Dict) -> Dict:
    """Creates a minimal but complete details structure from partial data"""
    details = {}
    
    # Ensure elements array exists
    if 'elements' in data:
        details['elements'] = data['elements']
    else:
        # Create a minimal elements structure
        elements = []
        # Try to extract element information from any available keys
        for key in data:
            if 'element' in key.lower() or 'component' in key.lower():
                elements.append(data[key])
        
        # If we couldn't extract elements, create a placeholder
        if not elements:
            elements = [{"type": "Unknown", "position": "Undetermined", 
                        "purpose": "Not specified", "styling": "Not specified",
                        "proportion": "Not specified"}]
                     
        details['elements'] = elements
    
    # Add all required fields, using data if available
    required_fields = ["visual_flow", "visual_tone", "color_strategy", 
                     "typography_approach", "spacing_technique", "engagement_mechanics", 
                     "conversion_elements", "best_practices", "primary_offering_visibility"]
    
    for field in required_fields:
        if field in data:
            details[field] = data[field]
        elif field == "best_practices" and "practices" in data:
            details[field] = data["practices"]
        elif field == "primary_offering_visibility":
            # Create valid structure for this complex field
            if field in data:
                details[field] = data[field]
            else:
                details[field] = {"is_visible": True, 
                                "description": "Visibility not explicitly analyzed"}
        elif field in data.get("details", {}):
            details[field] = data["details"][field]
        else:
            # Create placeholder values for missing fields
            if field == "best_practices":
                details[field] = ["Clear visual hierarchy", "Focused messaging"]
            else:
                details[field] = "Not explicitly analyzed"
    
    return details 