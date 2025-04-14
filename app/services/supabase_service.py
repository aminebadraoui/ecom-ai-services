import logging
from supabase import create_client, Client
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

class SupabaseService:
    """Service for interacting with Supabase"""
    
    def __init__(self):
        """Initialize the Supabase client"""
        try:
            if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
                logger.warning("Supabase credentials not found. Database features will not work.")
                self.client = None
            else:
                self.client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
                logger.info("Supabase client initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing Supabase client: {str(e)}")
            self.client = None
    
    def get_ad_concept_by_archive_id(self, ad_archive_id: str):
        """Get ad concept by ad_archive_id from the database"""
        if not self.client:
            logger.error("Supabase client not initialized. Cannot fetch ad concept.")
            return None
        
        try:
            response = self.client.table('ad_concepts').select('*').eq('ad_archive_id', ad_archive_id).execute()
            if response.data and len(response.data) > 0:
                logger.info(f"Found ad concept for ad_archive_id: {ad_archive_id}")
                return response.data[0]
            else:
                logger.info(f"No ad concept found for ad_archive_id: {ad_archive_id}")
                return None
        except Exception as e:
            logger.error(f"Error fetching ad concept for ad_archive_id {ad_archive_id}: {str(e)}")
            return None
    
    def store_ad_concept(self, ad_archive_id: str, image_url: str, concept_json: dict):
        """Store ad concept in the database"""
        if not self.client:
            logger.error("Supabase client not initialized. Cannot store ad concept.")
            return None
        
        try:
            data = {
                'ad_archive_id': ad_archive_id,
                'image_url': image_url,
                'concept_json': concept_json
            }
            response = self.client.table('ad_concepts').insert(data).execute()
            logger.info(f"Stored ad concept for ad_archive_id: {ad_archive_id}")
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error storing ad concept for ad_archive_id {ad_archive_id}: {str(e)}")
            return None
    
    def store_ad_recipe(self, ad_archive_id: str, image_url: str, sales_url: str, ad_concept_json: dict, sales_page_json: dict, recipe_prompt: str):
        """Store ad recipe in the database"""
        if not self.client:
            logger.error("Supabase client not initialized. Cannot store ad recipe.")
            return None
        
        try:
            data = {
                'ad_archive_id': ad_archive_id,
                'image_url': image_url,
                'sales_url': sales_url,
                'ad_concept_json': ad_concept_json,
                'sales_page_json': sales_page_json,
                'recipe_prompt': recipe_prompt
            }
            response = self.client.table('ad_recipes').insert(data).execute()
            logger.info(f"Stored ad recipe for ad_archive_id: {ad_archive_id}")
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error storing ad recipe for ad_archive_id {ad_archive_id}: {str(e)}")
            return None

# Create a singleton instance
supabase_service = SupabaseService() 