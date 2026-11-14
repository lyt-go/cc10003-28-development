"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

下钻联动相关的三张表也放在这里：
- drill_versions：路径启用时冻结的版本快照（每层维度名、每层度量、深度、默认展开层）
- dashboard_refs：看板发布时对路径版本的当前引用（同一看板仅保留一份）
- audit_logs：启用、关闭、发布、拦截等动作的审计记录（与快照同一份数据）

所有写动作都走 store_lock，保证同一路径重复启用、同一看板并发发布最终只有一份当前引用。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS

# 下钻版本、看板引用、审计日志不属于 seed 里的业务模块，单独登记表名。
LINK_TABLES = ("drill_versions", "dashboard_refs", "audit_logs")


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        for name in LINK_TABLES:
            self._tables[name] = []
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        """可重入锁：服务层在一个动作里可能同时写多张表，需要同一把锁串起来。"""
        return self._lock

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """事务占位：内存实现里即加锁；换成数据库后这里替换成真正的事务边界。"""
        with self._lock:
            yield

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in LINK_TABLES)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
