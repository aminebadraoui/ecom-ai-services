from celery import Celery
from app.core.config import settings

# Print debug info
print(f"Setting up Celery with Redis host: {settings.REDIS_HOST}")

celery_app = Celery(
    "worker",
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=settings.CELERY_RESULT_BACKEND,
    broker_transport_options={
        'visibility_timeout': 3600,
        'fanout_prefix': True,
        'fanout_patterns': True,
        'socket_connect_timeout': 5,
        'socket_timeout': 5,
        'retry_on_timeout': True,
    },
    redis_socket_timeout=5,
    redis_socket_connect_timeout=5,
    redis_retry_on_timeout=True,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.task_routes = {
    "app.tasks.ad_concept_tasks.*": {"queue": "ad-concept"},
    "app.tasks.sales_page_tasks.*": {"queue": "sales-page"},
    "app.tasks.ad_recipe_tasks.*": {"queue": "ad-recipe"},
}

celery_app.conf.update(
    task_track_started=True,
    result_expires=3600,  # 1 hour
    broker_connection_retry_on_startup=True,
)

# Import tasks modules to register tasks with Celery
import app.tasks.ad_concept_tasks
import app.tasks.sales_page_tasks
import app.tasks.ad_recipe_tasks 