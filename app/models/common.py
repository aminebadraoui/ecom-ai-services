from pydantic import BaseModel
from typing import Dict, Optional

class TaskResponse(BaseModel):
    task_id: str
    message: str

class TaskResult(BaseModel):
    status: str
    result: Optional[Dict] = None
    error: Optional[str] = None 