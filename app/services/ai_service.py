from app.services.ad_concept_service import process_extract_ad_concept, task_results as ad_concept_task_results
from app.services.sales_page_service import process_extract_sales_page, task_results as sales_page_task_results

# Combined task results dictionary
task_results = {}

# Update task results with values from service-specific dictionaries
def get_task_result(task_id: str):
    """Get a task result from any of the services"""
    if task_id in ad_concept_task_results:
        return ad_concept_task_results[task_id]
    elif task_id in sales_page_task_results:
        return sales_page_task_results[task_id]
    else:
        return None 