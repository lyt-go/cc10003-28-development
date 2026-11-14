"""多维下钻业务规则：状态流转、字段校验与筛选口径都收在这里。

启用路径时联动 drill_linkage 冻结版本快照；关闭路径时联动标记看板引用失效。
"""
from __future__ import annotations

from typing import Any

from app.services.drill_linkage import (
    PATH_CONFIG_FIELDS,
    linkage,
)
from app.store import store

MODULE = "datadrill"
REQUIRED_FIELDS = ["路径编号", "起始维度", "下钻层级"]
STATUS_ORDER = ["草稿", "路径评审中", "已启用", "已关闭"]
ACTION_RULES = {"提交路径评审": "路径评审中", "启用路径": "已启用", "关闭路径": "已关闭"}
NEGATIVE_ACTIONS = []


class DatadrillService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("路径编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记时若一并带上层级/度量配置，原样收下，启用时再做发布守卫校验并冻结。
        for field in PATH_CONFIG_FIELDS:
            if field not in entry and values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["当前版本号"] = None
        rows.append(entry)
        return entry, []

    def update_config(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """修订草稿/评审中的路径配置；已启用版本是冻结快照，不会被这里改动。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"下钻路径 {entry_id} 不存在或已归档"
        if entry.get("status") == "已启用":
            return None, "路径已启用，当前版本已冻结；如需调整请先关闭路径再修订后重新启用"
        changed = [field for field in PATH_CONFIG_FIELDS if values.get(field) is not None]
        if not changed:
            return None, "没有可修订的路径配置字段"
        with store.lock:
            for field in changed:
                entry[field] = values.get(field)
        return entry, f"已修订 {len(changed)} 项路径配置（尚未冻结，启用时生效）"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"下钻路径 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于多维下钻可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "启用路径":
            # 发布守卫：缺中间层、默认层超深、每层度量不一致等都在这里拦住，状态保持原样。
            _version, errors = linkage.enable_path(entry)
            if errors:
                return None, "启用被发布守卫拦截：" + "；".join(errors)
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            return entry, "下钻路径已启用，层级快照与默认展开层已冻结为新版本"

        if action == "关闭路径":
            linkage.close_path(entry)
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = True
            return entry, "下钻路径已关闭，引用该路径的看板已保留配置并标记失效来源"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"下钻路径已{action}"
