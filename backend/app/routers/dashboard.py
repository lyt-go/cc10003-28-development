"""看板配置接口：维护看板页面，覆盖提交看板评审、发布看板、下线看板等动作。

发布动作可携带 路径id/路径编号/版本id/publish_guard 引用已启用的下钻版本；
详情报表可从看板追到路径版本、下钻层级、默认展开层与审计快照。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dashboard import DashboardService
from app.services.drill_linkage import PUBLISH_GUARD, linkage

router = APIRouter(prefix="/api/dashboard", tags=["看板配置"])

service = DashboardService()

LIST_FIELDS = ["看板编号", "看板名称", "包含指标", "引用路径编号", "引用路径版本", "默认展开层", "引用状态", "失效原因", "新可用版本"]
STATUSES = ["草稿", "看板评审中", "已发布", "已下线"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按看板编号检索"),
    status: str | None = Query(default=None, description="草稿、看板评审中、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按看板编号与状态过滤看板配置列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/enabled-versions", response_model=dict)
def enabled_versions() -> dict[str, Any]:
    """发布弹窗用：列出当前所有已启用的下钻路径版本供选择。"""
    items = linkage.enabled_versions()
    return {"total": len(items), "items": items, "publish_guard": PUBLISH_GUARD}


@router.get("/audit-logs", response_model=dict)
def list_audit() -> dict[str, Any]:
    """看板侧审计记录：发布成功/拦截均与引用快照同一份内容。"""
    items = linkage.list_audit(module="dashboard")
    return {"total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出看板配置清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dashboard", "total": total, "items": items}


@router.get("/{entry_id}/enter", response_model=dict)
def enter_dashboard(entry_id: int) -> dict[str, Any]:
    """用户进入看板：按引用版本的默认展开层返回起始汇总与展开层级。"""
    view = linkage.enter_dashboard(entry_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"看板页面 {entry_id} 不存在或已归档")
    return view


@router.get("/{entry_id}/report", response_model=dict)
def dashboard_report(entry_id: int) -> dict[str, Any]:
    """看板详情报表：看板 → 当前引用 → 路径版本快照 → 审计记录的完整链路。"""
    report = linkage.dashboard_report(entry_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"看板页面 {entry_id} 不存在或已归档")
    return report


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条看板页面明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"看板页面 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条看板页面，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="看板页面已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条看板页面执行提交看板评审、发布看板、下线看板；不允许的动作会被拦下并说明原因。

    发布看板时可在 values 中携带：路径id / 路径编号 / 版本id / publish_guard。
    守卫不通过（无同版本启用层级等）时阻止发布并保留旧版本。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
