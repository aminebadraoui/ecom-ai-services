from celery import Celery
from app.core.config import settings

# Print debug info
print(f"Setting up Celery with Redis URL: {settings.REDIS_URL}")

celery_app = Celery(
    "worker",
    backend=settings.REDIS_URL,
    broker=settings.REDIS_URL,
)

celery_app.conf.task_routes = {
    "app.tasks.ad_concept_tasks.*": {"queue": "ad-concept"},
    "app.tasks.sales_page_tasks.*": {"queue": "sales-page"},
}

celery_app.conf.update(
    task_track_started=True,
    result_expires=3600,  # 1 hour
    broker_connection_retry_on_startup=True,
) 