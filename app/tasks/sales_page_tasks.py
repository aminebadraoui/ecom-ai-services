import json
import asyncio
import nest_asyncio
from celery import Task
from pydantic_ai import Agent
from redis import Redis
import os
import logging
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.sales_page import SalesPageOutput
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

class SalesPageTask(Task):
    """Base task for sales page extraction"""
    
    def update_state(self, task_id, status, result=None, error=None):
        """Update task state in Redis"""
        task_data = TaskResult(
            status=status,
            result=result,
            error=error
        ).model_dump()
        redis_client.set(f"task:{task_id}", json.dumps(task_data))

@celery_app.task(base=SalesPageTask, bind=True, name="app.tasks.sales_page_tasks.extract_sales_page")
def extract_sales_page(self, page_url: str, task_id: str):
    """
    Process a sales page extraction request and store the result in Redis
    
    Args:
        page_url: URL of the sales page to analyze
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
                result_type=SalesPageOutput,
                system_prompt="""You are an expert marketing assistant. Analyze the following sales page and extract all the essential information needed for an advertiser to create effective Facebook ad creatives. Organize the information into a structured JSON format.

Your analysis MUST include these fields in JSON format:
- product_name: The name of the product (required)
- tagline: Main tagline or slogan
- key_benefits: Array of key benefits of the product
- features: Array of product features
- problem_addressed: Problem the product addresses
- target_audience: Target audience description
- social_proof: Object containing testimonials (array), media_mentions (array), and sales_numbers (string)
- offer: Object containing discount, limited_time_offer, shipping, and guarantee details
- call_to_action: Call to action text
- visual_elements_to_include: Array of visual elements to include in ads
- brand_voice: Brand voice description
- compliance_notes: Any compliance or legal considerations

The output JSON MUST match this structure:
{
  "product_name": "Example Product",
  "tagline": "Revolutionize Your Experience",
  "key_benefits": ["Benefit 1", "Benefit 2", "Benefit 3"],
  "features": ["Feature 1", "Feature 2", "Feature 3"],
  "problem_addressed": "Description of the problem this product solves",
  "target_audience": "Description of the target audience",
  "social_proof": {
    "testimonials": ["Testimonial 1", "Testimonial 2"],
    "media_mentions": ["Media mention 1", "Media mention 2"],
    "sales_numbers": "Over 10,000 satisfied customers"
  },
  "offer": {
    "discount": "20% off",
    "limited_time_offer": "Only available until Friday",
    "shipping": "Free shipping worldwide",
    "guarantee": "30-day money-back guarantee"
  },
  "call_to_action": "Get yours today",
  "visual_elements_to_include": ["Product image", "Customer testimonial image", "Before/after comparison"],
  "brand_voice": "Professional but approachable, emphasizes expertise",
  "compliance_notes": "Must include disclaimer about results varying by individual"
}

Be comprehensive and identify all marketing elements that would be useful for creating compelling ads.
"""
            )
            
            # Process the sales page
            result = await agent.run(f"Analyze the sales page at this URL: {page_url} and extract information in the exact JSON format specified.")
            
            return result
        
        # Create a new event loop and run the async function
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(process_with_agent())
        
        # Store the successful result
        result_dict = result.data.model_dump()
         # Log the result
        logger.info(f"Final result data ({task_id}): {json.dumps(result.data.model_dump(), indent=2)}")
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
        print(f"Error processing task {task_id}: {error_details}")
        
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