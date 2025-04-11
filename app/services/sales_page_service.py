from pydantic_ai import Agent
from app.models.sales_page import SalesPageOutput
from app.models.common import TaskResult

# Create agent for sales page extraction
sales_page_agent = Agent(
    "openai:gpt-4o",
    result_type=SalesPageOutput,
    system_prompt="""You are an expert marketing assistant. Analyze the following sales page and extract all the essential information needed for an advertiser to create effective Facebook ad creatives. Organize the information into a structured JSON format.

Extract the following key details:
- Product name
- Tagline or main slogan
- Key benefits of the product
- Product features
- Problem the product addresses
- Target audience description
- Social proof (testimonials, media mentions, sales numbers)
- Offer details (discount, limited-time offer, shipping, guarantee)
- Call to action text
- Visual elements to include in ads
- Brand voice description
- Compliance notes

Be comprehensive and identify all marketing elements that would be useful for creating compelling ads.
"""
)

# Storage for sales page task results
task_results = {}

async def process_extract_sales_page(page_url: str, task_id: str):
    """Process a sales page extraction request and store the result"""
    try:
        result = await sales_page_agent.run(f"Analyze the sales page at this URL: {page_url}")
        task_results[task_id] = TaskResult(status="completed", result=result.data.model_dump())
    except Exception as e:
        task_results[task_id] = TaskResult(status="failed", error=str(e)) 