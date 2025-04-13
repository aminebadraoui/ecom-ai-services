import json
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from redis import Redis
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings

router = APIRouter()

# Initialize Redis client
redis_client = Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True
)

# For debugging
print(f"[API] Connecting to Redis at {settings.REDIS_URL}")

@router.get("/tasks/{task_id}")
async def get_task_result_endpoint(task_id: str):
    """Get the result of a task by its ID"""
    task_result = redis_client.get(f"task:{task_id}")
    
    if not task_result:
        return JSONResponse(
            status_code=404,
            content={"error": "Task not found"}
        )
    
    return json.loads(task_result)

@router.get("/tasks/{task_id}/stream")
async def stream_task_result(task_id: str):
    """Stream task results using Server-Sent Events"""
    async def event_generator():
        # Initial delay to allow task to be registered
        await asyncio.sleep(0.5)
        
        # Check for 60 seconds (adjust as needed)
        for _ in range(120):  # 120 * 0.5s = 60s
            task_result = redis_client.get(f"task:{task_id}")
            
            if task_result:
                task_data = json.loads(task_result)
                
                # Send the current state as an event
                yield {
                    "event": "update",
                    "data": task_result
                }
                
                # If the task is completed or failed, stop streaming
                if task_data.get("status") in ["completed", "failed"]:
                    break
            
            # Wait before checking again
            await asyncio.sleep(0.5)
        
        # Send a final event if we time out
        if not task_result or json.loads(task_result).get("status") not in ["completed", "failed"]:
            yield {
                "event": "timeout",
                "data": json.dumps({"status": "timeout", "error": "Task processing timed out"})
            }
    
    return EventSourceResponse(event_generator()) 