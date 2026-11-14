"""多维下钻接口：维护下钻路径，覆盖提交路径评审、启用路径、关闭路径等动作。

启用路径会冻结路径版本快照；另提供版本列表、修订配置与路径报表，支撑看板侧追溯。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.datadrill import DatadrillService
from app.services.drill_linkage import linkage

router = APIRouter(prefix="/api/datadrill", tags=["多维下钻"])

service = DatadrillService()

LIST_FIELDS = ["路径编号", "起始维度", "下钻层级", "每层维度名", "每层度量", "路径深度", "默认展开层", "当前版本号", "关联看板"]
STATUSES = ["草稿", "路径评审中", "已启用", "已关闭"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按路径编号检索"),
    status: str | None = Query(default=None, description="草稿、路径评审中、已启用、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按路径编号与状态过滤多维下钻列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/versions", response_model=dict)
def list_versions(
    path_id: int | None = Query(default=None, description="按路径 id 过滤版本"),
    status: str | None = Query(default=None, description="已启用、已关闭、已替代"),
) -> dict[str, Any]:
    """查看下钻路径的冻结版本清单（含每层维度名、每层度量、深度、默认展开层）。"""
    items = linkage.list_versions(path_id=path_id, status=status)
    return {"total": len(items), "items": items}


@router.get("/audit-logs", response_model=dict)
def list_audit() -> dict[str, Any]:
    """下钻路径侧审计记录：启用/关闭/拦截均含同一版本快照。"""
    items = linkage.list_audit(module="datadrill")
    return {"total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出多维下钻清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "datadrill", "total": total, "items": items}


@router.get("/{entry_id}/report", response_model=dict)
def path_report(entry_id: int) -> dict[str, Any]:
    """路径报表：路径条目、全部版本、被哪些看板引用、审计快照一站追溯。"""
    report = linkage.path_report(entry_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"下钻路径 {entry_id} 不存在或已归档")
    return report


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条下钻路径明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"下钻路径 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条下钻路径，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="下钻路径已登记", entry=entry)


@router.patch("/{entry_id}/config", response_model=ActionResult)
def update_config(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修订未启用路径的层级配置；已冻结版本不受影响，重新启用才产生新版本。"""
    entry, message = service.update_config(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条下钻路径执行提交路径评审、启用路径、关闭路径；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
