"""下钻路径 ↔ 看板发布联动核心。

真实数据流（本需求的版本冻结范围据此确定）：
1. 下钻路径「启用路径」时，按路径当前配置冻结一份版本快照——每层维度名、每层度量、
   路径深度、默认展开层，连同路径编号/起始维度写入 drill_versions；后续路径再怎么改，
   已冻结版本不变。
2. 看板「发布看板」时只能引用同一条路径下 status=已启用 的版本，守卫通过后把同一份
   快照复制到 dashboard_refs 与看板条目上；看板与路径由此共享默认展开层与路径版本。
3. 路径「关闭路径」时不删看板配置，只把引用标记为失效、记录失效来源，并指向同路径
   新的可用版本（若已重新启用）。
4. 每个动作写 audit_logs，审计里的快照与版本/引用快照是同一份内容。

全部写操作在 store.lock 内完成，保证同一路径重复启用、同一看板并发发布最终只保留
一份当前引用。
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

from app.store import store

DRILL_MODULE = "datadrill"
DASHBOARD_MODULE = "dashboard"
VERSION_TABLE = "drill_versions"
REF_TABLE = "dashboard_refs"
AUDIT_TABLE = "audit_logs"

# 发布守卫令牌：所有版本/引用/审计记录都带上，入参显式传了 publish_guard 时必须与之匹配。
PUBLISH_GUARD = "R28-A9F3-L2-D7C5"

# 发布判定的明确规则与阈值，不合规直接拦截、不允许保存。
MIN_PATH_DEPTH = 1
MAX_PATH_DEPTH = 8
MIN_DEFAULT_LAYER = 1
MAX_MEASURES_PER_LAYER = 5

# 可在登记/修订接口维护的路径配置字段（状态字段不在其中，只能走动作流转）。
PATH_CONFIG_FIELDS = ["起始维度", "下钻层级", "每层维度名", "每层度量", "路径深度", "默认展开层"]

LAYER_SPLIT_RE = re.compile(r"[|｜;；\n]+")
ITEM_SPLIT_RE = re.compile(r"[，,、+／/\s]+")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _split_items(value: Any) -> list[str]:
    """把逗号/顿号/斜杠等分隔的字符串或数组拆成去空白后的非空项。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for part in value:
            items.extend(_split_items(part))
        return items
    return [item.strip() for item in ITEM_SPLIT_RE.split(str(value)) if item.strip()]


def _split_layers(value: Any) -> list[list[str]]:
    """先按层分隔符（| ；换行）拆层，再在每层内拆度量项；数组输入按元素逐层处理。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        layers: list[list[str]] = []
        for part in value:
            if isinstance(part, (list, tuple)):
                layer = _split_items(part)
            else:
                text = str(part)
                if LAYER_SPLIT_RE.search(text):
                    layers.extend(_split_layers(text))
                    continue
                layer = _split_items(text)
            if layer:
                layers.append(layer)
        return layers
    chunks = [chunk for chunk in LAYER_SPLIT_RE.split(str(value)) if chunk.strip()]
    return [_split_items(chunk) for chunk in chunks]


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def validate_path_config(entry: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """路径启用前的发布守卫校验，返回解析后的冻结素材与错误说明。

    规则：路径深度 1~8 且与每层维度名层数一致、不得缺中间层；默认展开层 1~深度；
    每层度量 1~5 项且各层度量集合一致；起始维度必须等于第一层维度名。
    """
    errors: list[str] = []
    dims = _split_items(entry.get("每层维度名"))
    depth_declared = _to_int(entry.get("路径深度"))
    default_layer = _to_int(entry.get("默认展开层"))
    measure_layers = _split_layers(entry.get("每层度量"))
    start_dim = str(entry.get("起始维度") or "").strip()

    if not dims:
        errors.append("每层维度名为空，无法冻结下钻层级")
    if len(dims) != len(set(dims)):
        errors.append("每层维度名存在重复层级，下钻层级必须唯一")

    depth = len(dims)
    if depth_declared is None:
        errors.append("路径深度必须是整数")
    elif not (MIN_PATH_DEPTH <= depth_declared <= MAX_PATH_DEPTH):
        errors.append(f"路径深度 {depth_declared} 超出允许范围 {MIN_PATH_DEPTH}~{MAX_PATH_DEPTH}")
    elif depth_declared != depth:
        errors.append(
            f"路径深度声明为 {depth_declared}，但每层维度名只有 {depth} 层，缺少中间层级"
        )

    if not start_dim:
        errors.append("起始维度为空")
    elif dims and start_dim != dims[0]:
        errors.append(f"起始维度「{start_dim}」必须与第一层维度名「{dims[0]}」一致")

    if not measure_layers:
        errors.append("每层度量为空，至少需要一层度量定义")
    elif len(measure_layers) != depth:
        errors.append(f"每层度量给出 {len(measure_layers)} 层，与路径深度 {depth} 不一致，缺少中间层级")
    else:
        for index, layer in enumerate(measure_layers, start=1):
            if not layer:
                errors.append(f"第 {index} 层缺少度量定义")
            elif len(layer) > MAX_MEASURES_PER_LAYER:
                errors.append(f"第 {index} 层度量 {len(layer)} 项，超过每层度量上限 {MAX_MEASURES_PER_LAYER}")
        base_set = set(measure_layers[0]) if measure_layers and measure_layers[0] else set()
        for index, layer in enumerate(measure_layers[1:], start=2):
            if base_set and set(layer) != base_set:
                errors.append(
                    f"第 {index} 层度量 {sorted(set(layer))} 与第 1 层 {sorted(base_set)} 不一致，每层度量必须保持一致"
                )
                break

    if default_layer is None:
        errors.append("默认展开层必须是整数")
    elif depth and not (MIN_DEFAULT_LAYER <= default_layer <= depth):
        errors.append(f"默认展开层 {default_layer} 超出允许范围 {MIN_DEFAULT_LAYER}~{depth}")

    if errors:
        return None, errors

    parsed = {
        "路径编号": str(entry.get("路径编号") or "").strip(),
        "起始维度": start_dim,
        "下钻层级": list(dims),
        "每层维度名": list(dims),
        "每层度量": [list(layer) for layer in measure_layers],
        "路径深度": depth,
        "默认展开层": int(default_layer),  # type: ignore[arg-type]
    }
    return parsed, []


def _signature(parsed: dict[str, Any]) -> str:
    """同一配置重复启用时据此识别幂等，避免产生重复版本。"""
    return repr((
        parsed["每层维度名"],
        parsed["每层度量"],
        parsed["路径深度"],
        parsed["默认展开层"],
    ))


def _write_audit(
    *,
    module: str,
    action: str,
    target_id: int,
    target_code: str,
    result: str,
    snapshot: dict[str, Any] | None,
    detail: str,
) -> dict[str, Any]:
    row = {
        "id": store.next_id(AUDIT_TABLE),
        "模块": module,
        "动作": action,
        "对象id": target_id,
        "对象编号": target_code,
        "结果": result,
        "publish_guard": PUBLISH_GUARD,
        "快照": copy.deepcopy(snapshot) if snapshot is not None else None,
        "说明": detail,
        "时间": _now(),
    }
    store.rows(AUDIT_TABLE).append(row)
    return row


def _find_enabled_version(path_id: int) -> dict[str, Any] | None:
    for row in store.rows(VERSION_TABLE):
        if int(row["路径id"]) == path_id and row["status"] == "已启用":
            return row
    return None


def _mark_refs_for_path(path_id: int, reason: str) -> None:
    """路径关闭/新版本产生后，同步刷新引用该路径的看板标记与新可用版本指引。"""
    latest = _find_enabled_version(path_id)
    latest_code = latest["版本号"] if latest else None
    for ref in store.rows(REF_TABLE):
        if int(ref["路径id"]) != path_id or ref["引用状态"] != "生效":
            continue
        ref["引用状态"] = "失效"
        ref["失效原因"] = reason
        ref["新可用版本"] = latest_code
        dashboard = store.find(DASHBOARD_MODULE, int(ref["看板id"]))
        if dashboard is not None:
            dashboard["引用状态"] = "失效"
            dashboard["失效原因"] = reason
            dashboard["新可用版本"] = latest_code


def _refresh_available_version(path_id: int) -> None:
    """路径重新启用后，把新版本号回填给已失效引用，作为新可用版本指引。"""
    latest = _find_enabled_version(path_id)
    if latest is None:
        return
    for ref in store.rows(REF_TABLE):
        if int(ref["路径id"]) != path_id:
            continue
        if ref["引用状态"] == "失效" and ref["版本号"] != latest["版本号"]:
            ref["新可用版本"] = latest["版本号"]
        dashboard = store.find(DASHBOARD_MODULE, int(ref["看板id"]))
        if dashboard is not None and dashboard.get("引用状态") == "失效":
            dashboard["新可用版本"] = latest["版本号"]


class DrillLinkageService:
    # ---------- 下钻路径侧 ----------
    def list_versions(
        self,
        *,
        path_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(VERSION_TABLE)
        if path_id is not None:
            rows = [row for row in rows if int(row["路径id"]) == path_id]
        if status:
            rows = [row for row in rows if row["status"] == status]
        return rows

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        return store.find(VERSION_TABLE, version_id)

    def enable_path(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """启用路径：校验通过后冻结版本快照；同配置重复启用幂等返回当前版本。"""
        parsed, errors = validate_path_config(entry)
        if errors:
            with store.lock:
                _write_audit(
                    module=DRILL_MODULE,
                    action="启用路径",
                    target_id=int(entry["id"]),
                    target_code=str(entry.get("路径编号", "")),
                    result="拦截",
                    snapshot=entry,
                    detail="启用被守卫拦截：" + "；".join(errors),
                )
            return None, errors

        with store.lock:
            signature = _signature(parsed)
            current = _find_enabled_version(int(entry["id"]))
            if current is not None and current.get("签名") == signature:
                # 同路径重复启用且配置未变：不产生新版本，保持一份当前引用。
                entry["当前版本号"] = current["版本号"]
                _write_audit(
                    module=DRILL_MODULE,
                    action="启用路径",
                    target_id=int(entry["id"]),
                    target_code=parsed["路径编号"],
                    result="成功",
                    snapshot=current["快照"],
                    detail=f"配置未变化，复用已启用版本 {current['版本号']}",
                )
                return current, []

            if current is not None:
                current["status"] = "已替代"
                current["停用时间"] = _now()

            seq = sum(1 for row in store.rows(VERSION_TABLE) if int(row["路径id"]) == int(entry["id"])) + 1
            snapshot = copy.deepcopy(parsed)
            version = {
                "id": store.next_id(VERSION_TABLE),
                "路径id": int(entry["id"]),
                "路径编号": parsed["路径编号"],
                "版本号": f"V{seq}",
                "status": "已启用",
                "签名": signature,
                "起始维度": parsed["起始维度"],
                "下钻层级": list(parsed["下钻层级"]),
                "每层维度名": list(parsed["每层维度名"]),
                "每层度量": copy.deepcopy(parsed["每层度量"]),
                "路径深度": parsed["路径深度"],
                "默认展开层": parsed["默认展开层"],
                "publish_guard": PUBLISH_GUARD,
                "快照": snapshot,
                "启用时间": _now(),
                "停用时间": None,
            }
            store.rows(VERSION_TABLE).append(version)

            # 把冻结结果回写到路径条目，路径与看板共享同一份默认展开层/层级口径。
            entry["起始维度"] = parsed["起始维度"]
            entry["下钻层级"] = list(parsed["下钻层级"])
            entry["每层维度名"] = list(parsed["每层维度名"])
            entry["每层度量"] = copy.deepcopy(parsed["每层度量"])
            entry["路径深度"] = parsed["路径深度"]
            entry["默认展开层"] = parsed["默认展开层"]
            entry["当前版本号"] = version["版本号"]
            entry["版本快照"] = copy.deepcopy(snapshot)

            _refresh_available_version(int(entry["id"]))
            _write_audit(
                module=DRILL_MODULE,
                action="启用路径",
                target_id=int(entry["id"]),
                target_code=parsed["路径编号"],
                result="成功",
                snapshot=snapshot,
                detail=f"已冻结版本 {version['版本号']}（深度 {parsed['路径深度']}，默认展开层 {parsed['默认展开层']}）",
            )
            return version, []

    def close_path(self, entry: dict[str, Any]) -> dict[str, Any]:
        """关闭路径：版本置为已关闭，引用看板保留原配置但标记失效来源与新可用版本。"""
        path_id = int(entry["id"])
        with store.lock:
            closed_versions: list[str] = []
            for version in store.rows(VERSION_TABLE):
                if int(version["路径id"]) == path_id and version["status"] == "已启用":
                    version["status"] = "已关闭"
                    version["停用时间"] = _now()
                    closed_versions.append(version["版本号"])
            reason = f"来源路径 {entry.get('路径编号', '')} 已关闭"
            _mark_refs_for_path(path_id, reason)
            entry["当前版本号"] = None
            _write_audit(
                module=DRILL_MODULE,
                action="关闭路径",
                target_id=path_id,
                target_code=str(entry.get("路径编号", "")),
                result="成功",
                snapshot=None,
                detail="已关闭版本：" + ("、".join(closed_versions) if closed_versions else "无启用中的版本"),
            )
        return entry

    # ---------- 看板侧 ----------
    def enabled_versions(self) -> list[dict[str, Any]]:
        return [row for row in store.rows(VERSION_TABLE) if row["status"] == "已启用"]

    def publish_dashboard(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """看板发布守卫：只允许引用同路径的已启用版本，校验通过才冻结引用快照。"""
        guard = str(values.get("publish_guard") or "").strip()
        if guard and guard != PUBLISH_GUARD:
            return None, [f"publish_guard 不匹配，发布守卫令牌校验失败"]

        path_id = _to_int(values.get("路径id"))
        path_code = str(values.get("路径编号") or "").strip()
        version_id = _to_int(values.get("版本id"))

        with store.lock:
            version: dict[str, Any] | None = None
            if version_id is not None:
                candidate = self.get_version(version_id)
                if candidate is not None and candidate["status"] == "已启用":
                    version = candidate
            elif path_id is not None:
                candidate = _find_enabled_version(path_id)
                if candidate is not None:
                    version = candidate
            elif path_code:
                for row in store.rows(DRILL_MODULE):
                    if str(row.get("路径编号")) == path_code:
                        candidate = _find_enabled_version(int(row["id"]))
                        if candidate is not None:
                            version = candidate
                        break

            if version is None:
                # 未引用下钻路径的看板沿用原有发布流程；一旦填了引用却找不到启用版本则拦截。
                if path_id is None and not path_code and version_id is None:
                    entry["引用状态"] = "未引用"
                    _write_audit(
                        module=DASHBOARD_MODULE,
                        action="发布看板",
                        target_id=int(entry["id"]),
                        target_code=str(entry.get("看板编号", "")),
                        result="成功",
                        snapshot=None,
                        detail="未引用下钻路径，按普通看板发布",
                    )
                    return entry, []
                errors = ["引用的下钻路径不存在同版本的已启用层级，看板发布只能引用已启用版本"]
                _write_audit(
                    module=DASHBOARD_MODULE,
                    action="发布看板",
                    target_id=int(entry["id"]),
                    target_code=str(entry.get("看板编号", "")),
                    result="拦截",
                    snapshot=None,
                    detail="发布被守卫拦截：" + "；".join(errors),
                )
                return None, errors

            # 再跑一次快照守卫，保证启用后被篡改的内存数据也无法蒙混发布。
            path_entry = store.find(DRILL_MODULE, int(version["路径id"]))
            if path_entry is None:
                return None, ["来源下钻路径已不存在，无法发布"]

            snapshot = copy.deepcopy(version["快照"])
            now = _now()
            existing = next(
                (row for row in store.rows(REF_TABLE) if int(row["看板id"]) == int(entry["id"])),
                None,
            )
            if existing is None:
                ref = {
                    "id": store.next_id(REF_TABLE),
                    "看板id": int(entry["id"]),
                    "创建时间": now,
                }
                store.rows(REF_TABLE).append(ref)
            else:
                ref = existing
            ref.update({
                "看板编号": entry.get("看板编号"),
                "路径id": int(version["路径id"]),
                "路径编号": version["路径编号"],
                "版本id": int(version["id"]),
                "版本号": version["版本号"],
                "引用状态": "生效",
                "失效原因": None,
                "新可用版本": None,
                "起始维度": snapshot["起始维度"],
                "下钻层级": list(snapshot["下钻层级"]),
                "每层维度名": list(snapshot["每层维度名"]),
                "每层度量": copy.deepcopy(snapshot["每层度量"]),
                "路径深度": snapshot["路径深度"],
                "默认展开层": snapshot["默认展开层"],
                "publish_guard": PUBLISH_GUARD,
                "快照": copy.deepcopy(snapshot),
                "更新时间": now,
            })

            # 看板条目回写：看板页面与下钻路径共享默认展开层与路径版本。
            entry["引用路径编号"] = version["路径编号"]
            entry["引用路径版本"] = version["版本号"]
            entry["引用版本id"] = int(version["id"])
            entry["引用状态"] = "生效"
            entry["失效原因"] = None
            entry["新可用版本"] = None
            entry["默认展开层"] = snapshot["默认展开层"]
            entry["路径深度"] = snapshot["路径深度"]
            entry["下钻层级"] = list(snapshot["下钻层级"])
            entry["每层维度名"] = list(snapshot["每层维度名"])
            entry["每层度量"] = copy.deepcopy(snapshot["每层度量"])
            entry["版本快照"] = copy.deepcopy(snapshot)

            _write_audit(
                module=DASHBOARD_MODULE,
                action="发布看板",
                target_id=int(entry["id"]),
                target_code=str(entry.get("看板编号", "")),
                result="成功",
                snapshot=snapshot,
                detail=(
                    f"已引用 {version['路径编号']} {version['版本号']}，"
                    f"深度 {snapshot['路径深度']}，默认展开层 {snapshot['默认展开层']}"
                ),
            )
            return entry, []

    def enter_dashboard(self, dashboard_id: int) -> dict[str, Any] | None:
        """用户进入看板时的视图：按冻结的默认展开层给出起始汇总与展开层级。

        例：region→channel→store、默认展开层 2，则展示 region 汇总并展开到 channel。
        引用已失效时仍返回原冻结快照（保留旧版本），同时带上失效来源与新可用版本。
        """
        entry = store.find(DASHBOARD_MODULE, dashboard_id)
        if entry is None:
            return None
        ref = next(
            (row for row in store.rows(REF_TABLE) if int(row["看板id"]) == dashboard_id),
            None,
        )
        if ref is None:
            return {
                "看板编号": entry.get("看板编号"),
                "引用状态": entry.get("引用状态", "未引用"),
                "提示": "该看板未引用下钻路径，按普通看板渲染",
            }
        snapshot = ref["快照"]
        depth = int(snapshot["路径深度"])
        default_layer = int(snapshot["默认展开层"])
        dims = list(snapshot["每层维度名"])
        return {
            "看板编号": entry.get("看板编号"),
            "路径编号": ref["路径编号"],
            "版本号": ref["版本号"],
            "引用状态": ref["引用状态"],
            "失效原因": ref.get("失效原因"),
            "新可用版本": ref.get("新可用版本"),
            "起始维度": snapshot["起始维度"],
            "汇总维度": dims[0],
            "默认展开层": default_layer,
            "路径深度": depth,
            "展开到维度": dims[default_layer - 1],
            "展开层级": dims[:default_layer],
            "全部层级": dims,
            "每层度量": snapshot["每层度量"],
            "publish_guard": PUBLISH_GUARD,
        }

    def list_refs(self, *, dashboard_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(REF_TABLE)
        if dashboard_id is not None:
            rows = [row for row in rows if int(row["看板id"]) == dashboard_id]
        return rows

    def dashboard_report(self, dashboard_id: int) -> dict[str, Any] | None:
        """看板详情报表：从看板一路追到路径版本、下钻层级、默认展开层与审计快照。"""
        entry = store.find(DASHBOARD_MODULE, dashboard_id)
        if entry is None:
            return None
        ref = next(
            (row for row in store.rows(REF_TABLE) if int(row["看板id"]) == dashboard_id),
            None,
        )
        version = None
        if ref is not None:
            version = self.get_version(int(ref["版本id"]))
        audits = [
            row for row in store.rows(AUDIT_TABLE)
            if row["模块"] == DASHBOARD_MODULE and int(row["对象id"]) == dashboard_id
        ]
        return {
            "看板": dict(entry),
            "当前引用": copy.deepcopy(ref),
            "路径版本": copy.deepcopy(version),
            "审计记录": copy.deepcopy(audits),
        }

    def path_report(self, path_id: int) -> dict[str, Any] | None:
        """下钻路径侧报表：路径条目 + 全部冻结版本 + 被哪些看板引用。"""
        entry = store.find(DRILL_MODULE, path_id)
        if entry is None:
            return None
        versions = self.list_versions(path_id=path_id)
        refs = [row for row in store.rows(REF_TABLE) if int(row["路径id"]) == path_id]
        audits = [
            row for row in store.rows(AUDIT_TABLE)
            if row["模块"] == DRILL_MODULE and int(row["对象id"]) == path_id
        ]
        return {
            "路径": dict(entry),
            "版本列表": copy.deepcopy(versions),
            "看板引用": copy.deepcopy(refs),
            "审计记录": copy.deepcopy(audits),
        }

    def list_audit(self, *, module: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(AUDIT_TABLE)
        if module:
            rows = [row for row in rows if row["模块"] == module]
        return rows


linkage = DrillLinkageService()
