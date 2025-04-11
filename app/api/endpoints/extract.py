from fastapi import APIRouter

from app.api.endpoints.ad_concept import router as ad_concept_router
from app.api.endpoints.sales_page import router as sales_page_router
from app.api.endpoints.tasks import router as tasks_router

router = APIRouter()

# Include all extraction-related routers
router.include_router(ad_concept_router)
router.include_router(sales_page_router)
router.include_router(tasks_router) 