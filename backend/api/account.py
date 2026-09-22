from fastapi import APIRouter, Request, HTTPException, status
from backend.storage.checkpoint import SQLiteCheckpointStorage
from backend.agent.orchestrator import workflow_store

account_router = APIRouter(tags=["account"])

@account_router.delete("/account")
async def delete_account(request: Request):
    """GDPR Article 17: Right to erasure. Deletes all workflows owned by the caller."""
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id:
        raise HTTPException(status_code=401, detail="Owner identity required")
    
    deleted_count = workflow_store.delete_by_owner(owner_id)
    return {"deleted_workflows": deleted_count, "owner_id": owner_id, "status": "purged"}
