from pydantic_ai import Agent, ImageUrl
from app.models.ad_concept import AdConceptOutput
from app.models.common import TaskResult

# Create agent for ad concept extraction
ad_concept_agent = Agent(
    "openai:gpt-4o",
    result_type=AdConceptOutput,
    system_prompt="""You are analyzing a product image intended for use on a product detail page.

Your task is to generate an extremely detailed and structured description of this image in JSON format. Focus on its layout, visual hierarchy, components, spacing, balance, and design purpose. Explain how each element contributes to the overall effectiveness of the image from a UX, marketing, and visual communication perspective.

Do not reference specific product details or branding (e.g., names, logos, text, or images unique to a particular product). Instead, abstract each component into a reusable format that could be applied to any type of product (e.g., "product photo area," "badge for product feature," "call-to-action button").

The output JSON should:
- Describe each element's purpose, position, relative size, and styling
- Explain the visual flow and what draws the user's attention first
- Include notes on visual tone/vibe (e.g., clean, premium, playful, rugged)
- Emphasize best practices for persuasive product imagery
- Be completely adaptable across product categories (e.g., pet products, electronics, skincare)

The goal is to create a modular visual template that communicates value, builds trust, and encourages engagement—regardless of the product type.
"""
)

# Storage for ad concept task results
task_results = {}

async def process_extract_ad_concept(image_url: str, task_id: str):
    """Process an ad concept extraction request and store the result"""
    try:
        # Use multimodal input with the image URL
        print(f"Processing ad concept extraction for image URL: {image_url}")
        result = await ad_concept_agent.run([
            "Analyze this product image and provide a detailed structured description.",
            ImageUrl(url=image_url)
        ])
        task_results[task_id] = TaskResult(status="completed", result=result.data.model_dump())
    except Exception as e:
        task_results[task_id] = TaskResult(status="failed", error=str(e)) 