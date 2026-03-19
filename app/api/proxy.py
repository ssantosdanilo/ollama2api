"""代理管理 API"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import require_admin
from app.services.proxy_manager import proxy_manager

router = APIRouter()


class SubscriptionRequest(BaseModel):
    url: str
    name: str = ""

class AddNodeRequest(BaseModel):
    name: str
    protocol: str
    server: str
    port: int
    config: dict = {}

class EnabledRequest(BaseModel):
    enabled: bool

class AutoSelectRequest(BaseModel):
    auto_select: bool

class SelectNodeRequest(BaseModel):
    node_id: Optional[str] = None


@router.get("/status")
async def get_status(_=Depends(require_admin)):
    return proxy_manager.get_status()

@router.get("/nodes")
async def get_nodes(_=Depends(require_admin)):
    return {"nodes": proxy_manager.get_nodes()}

@router.get("/subscriptions")
async def get_subscriptions(_=Depends(require_admin)):
    return {"subscriptions": proxy_manager.get_subscriptions()}

@router.post("/subscribe")
async def add_subscription(req: SubscriptionRequest, _=Depends(require_admin)):
    return await proxy_manager.add_subscription(req.url, req.name)

@router.delete("/subscribe")
async def remove_subscription(req: SubscriptionRequest, _=Depends(require_admin)):
    return await proxy_manager.remove_subscription(req.url)

@router.post("/nodes")
async def add_node(req: AddNodeRequest, _=Depends(require_admin)):
    return await proxy_manager.add_node(req.name, req.protocol, req.server, req.port, req.config)

@router.delete("/nodes/{node_id:path}")
async def remove_node(node_id: str, _=Depends(require_admin)):
    ok = await proxy_manager.remove_node(node_id)
    return {"success": ok}

@router.post("/test/{node_id:path}")
async def test_node(node_id: str, _=Depends(require_admin)):
    return await proxy_manager.test_node(node_id)

@router.post("/test-all")
async def test_all(_=Depends(require_admin)):
    return await proxy_manager.test_all()

@router.put("/enabled")
async def set_enabled(req: EnabledRequest, _=Depends(require_admin)):
    await proxy_manager.set_enabled(req.enabled)
    return {"success": True}

@router.put("/auto-select")
async def set_auto_select(req: AutoSelectRequest, _=Depends(require_admin)):
    await proxy_manager.set_auto_select(req.auto_select)
    return {"success": True}

@router.put("/select")
async def select_node(req: SelectNodeRequest, _=Depends(require_admin)):
    await proxy_manager.set_selected(req.node_id)
    return {"success": True}


@router.post("/smart-select")
async def smart_select(_=Depends(require_admin)):
    return await proxy_manager.smart_select_node()
