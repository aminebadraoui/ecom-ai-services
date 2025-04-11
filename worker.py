import os

from app.core.celery_app import celery_app

# Make sure all tasks are imported for Celery to discover them
from app.tasks.ad_concept_tasks import extract_ad_concept
from app.tasks.sales_page_tasks import extract_sales_page

if __name__ == "__main__":
    # Run Celery worker
    celery_app.worker_main(["worker", "--loglevel=info", "-Q", "ad-concept,sales-page"]) 