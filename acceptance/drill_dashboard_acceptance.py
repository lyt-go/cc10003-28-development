"""下钻看板联动验收脚本（仅标准库，通过 HTTP 调用真实服务）。

覆盖：
1. 启用路径冻结版本（每层维度名/每层度量/路径深度/默认展开层）与 publish_guard
2. 发布守卫：缺中间层、默认层超深、每层度量不一致、度量超上限、未启用路径、错误令牌
3. 看板发布引用同版本启用层级，进入看板展示 region 汇总并展开 channel
4. 并发发布与重复启用最终仅保留一份当前引用
5. 关闭路径后看板保留旧配置、标记失效来源与新可用版本
6. 看板详情报表可追到路径版本、下钻层级、默认展开层，审计记录同一快照

用法：python3 -I drill_dashboard_acceptance.py http://127.0.0.1:8123
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
GUARD = "R28-A9F3-L2-D7C5"

_passed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        global _passed
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failures.append(f"{name} {detail}")
        print(f"  ✗ {name} {detail}")


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def action(module: str, entry_id: int, values: dict) -> dict:
    _, body = call("POST", f"/api/{module}/{entry_id}/actions", {"values": values})
    return body


print("== 1. 守卫拦截：不合规路径禁止启用并保留原状态 ==")
bad_before = call("GET", "/api/datadrill/5")[1]
check("不合规路径初始为草稿", bad_before["status"] == "草稿")
blocked = action("datadrill", 5, {"action": "启用路径"})
check("启用被拦截", blocked["ok"] is False, blocked.get("message", ""))
check("拦截原因覆盖缺中间层", "缺少中间层级" in blocked["message"])
check("拦截原因覆盖默认层超深", "默认展开层" in blocked["message"] and "超出允许范围" in blocked["message"])
check("拦截原因覆盖度量不一致", "每层度量必须保持一致" in blocked["message"])
bad_after = call("GET", "/api/datadrill/5")[1]
check("拦截后状态保留为草稿（未保存新版本）", bad_after["status"] == "草稿" and bad_after.get("当前版本号") is None)
audit = call("GET", "/api/datadrill/audit-logs")[1]["items"]
check("拦截写入审计且带守卫令牌", any(log["结果"] == "拦截" and log["publish_guard"] == GUARD for log in audit))

print("== 2. 启用 DATA-REGION_CHANNEL：冻结 V1 快照 ==")
enabled = action("datadrill", 4, {"action": "启用路径"})
check("启用成功", enabled["ok"] is True, enabled.get("message", ""))
entry = enabled["entry"]
check("冻结默认展开层为 2", entry["默认展开层"] == 2)
check("冻结路径深度为 3", entry["路径深度"] == 3)
check("冻结三层维度名", entry["每层维度名"] == ["region", "channel", "store"])
check("每层度量一致且各 2 项", entry["每层度量"] == [["销售额", "订单量"]] * 3)
check("产生版本号 V1", entry["当前版本号"] == "V1")
versions = call("GET", "/api/datadrill/versions?path_id=4")[1]["items"]
check("版本列表仅 V1 且已启用", len(versions) == 1 and versions[0]["版本号"] == "V1" and versions[0]["status"] == "已启用")
check("版本快照带守卫令牌", versions[0]["publish_guard"] == GUARD)

print("== 3. 同路径重复启用幂等，不产生重复版本 ==")
action("datadrill", 4, {"action": "启用路径"})
action("datadrill", 4, {"action": "启用路径"})
versions = call("GET", "/api/datadrill/versions?path_id=4")[1]["items"]
check("重复启用后仍是一个版本", len(versions) == 1)

print("== 4. 看板发布守卫：错误令牌与未启用路径均拦截，旧状态保留 ==")
dash_before = call("GET", "/api/dashboard/4")[1]
wrong_guard = action("dashboard", 4, {"action": "发布看板", "路径id": 4, "publish_guard": "WRONG"})
check("错误令牌阻止发布", wrong_guard["ok"] is False and "publish_guard" in wrong_guard["message"])
not_enabled = action("dashboard", 4, {"action": "发布看板", "路径id": 1, "publish_guard": GUARD})
check("引用未启用路径被拦截", not_enabled["ok"] is False and "已启用" in not_enabled["message"])
dash_mid = call("GET", "/api/dashboard/4")[1]
check("拦截后看板仍为草稿、旧配置保留", dash_mid["status"] == "草稿" and dash_mid.get("引用状态") == "未引用")

print("== 5. DASH-SALES 发布引用同版本启用层级 ==")
published = action("dashboard", 4, {"action": "发布看板", "路径id": 4, "publish_guard": GUARD})
check("发布成功", published["ok"] is True, published.get("message", ""))
dash = published["entry"]
check("看板记录路径编号", dash["引用路径编号"] == "DATA-REGION_CHANNEL")
check("看板记录路径版本 V1", dash["引用路径版本"] == "V1")
check("看板共享默认展开层 2", dash["默认展开层"] == 2)
check("看板共享下钻层级", dash["下钻层级"] == ["region", "channel", "store"])
check("引用状态生效", dash["引用状态"] == "生效")

print("== 6. 并发发布最终只保留一份当前引用 ==")
errors: list[str] = []

def publish_concurrently() -> None:
    body = action("dashboard", 4, {"action": "发布看板", "版本id": 1, "publish_guard": GUARD})
    if not body["ok"]:
        errors.append(body["message"])

threads = [threading.Thread(target=publish_concurrently) for _ in range(10)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
check("并发发布全部成功", not errors, str(errors))
report = call("GET", "/api/dashboard/4/report")[1]
check("当前引用只有一份", len([report["当前引用"]]) == 1 and report["当前引用"]["版本号"] == "V1")
check("引用表幂等（报表只展示一条当前引用）", report["当前引用"]["id"] is not None)

print("== 7. 进入看板：region 汇总并展开 channel ==")
view = call("GET", "/api/dashboard/4/enter")[1]
check("起始汇总维度为 region", view["汇总维度"] == "region")
check("默认展开层为第 2 层", view["默认展开层"] == 2)
check("展开到维度为 channel", view["展开到维度"] == "channel")
check("展开层级为 region → channel", view["展开层级"] == ["region", "channel"])
check("全部层级含 store", view["全部层级"] == ["region", "channel", "store"])
check("进入视图带守卫令牌", view["publish_guard"] == GUARD)

print("== 8. 详情报表追溯：路径版本、层级、默认展开层与审计同一快照 ==")
check("报表含路径版本", report["路径版本"]["版本号"] == "V1")
check("报表含下钻层级", report["当前引用"]["下钻层级"] == ["region", "channel", "store"])
check("报表含默认展开层", report["当前引用"]["默认展开层"] == 2)
ref_snapshot = report["当前引用"]["快照"]
version_snapshot = report["路径版本"]["快照"]
check("引用快照与版本快照内容一致", ref_snapshot == version_snapshot)
publish_audits = [log for log in report["审计记录"] if log["动作"] == "发布看板" and log["结果"] == "成功"]
check("存在发布成功审计", bool(publish_audits))
check("审计快照与引用快照同一内容", publish_audits[-1]["快照"] == ref_snapshot)
check("审计记录带守卫令牌", publish_audits[-1]["publish_guard"] == GUARD)

print("== 9. 关闭路径：看板保留旧配置并标记失效来源与新可用版本 ==")
closed = action("datadrill", 4, {"action": "关闭路径"})
check("路径关闭成功", closed["ok"] is True)
report_closed = call("GET", "/api/dashboard/4/report")[1]
ref_closed = report_closed["当前引用"]
check("引用标记失效", ref_closed["引用状态"] == "失效")
check("记录失效来源", "DATA-REGION_CHANNEL" in ref_closed["失效原因"] and "关闭" in ref_closed["失效原因"])
check("旧版本与旧快照保留", ref_closed["版本号"] == "V1" and ref_closed["快照"]["默认展开层"] == 2)
check("关闭时暂无新可用版本", ref_closed["新可用版本"] is None)
view_closed = call("GET", "/api/dashboard/4/enter")[1]
check("进入看板仍返回旧快照且带失效标记", view_closed["引用状态"] == "失效" and view_closed["展开到维度"] == "channel")
dash_closed = call("GET", "/api/dashboard/4")[1]
check("看板列表同步失效标记", dash_closed["引用状态"] == "失效" and bool(dash_closed["失效原因"]))

print("== 10. 重新启用产生 V2，失效引用指向新可用版本 ==")
re_enabled = action("datadrill", 4, {"action": "启用路径"})
check("重新启用成功且版本为 V2", re_enabled["ok"] is True and re_enabled["entry"]["当前版本号"] == "V2")
versions = call("GET", "/api/datadrill/versions?path_id=4")[1]["items"]
check("版本历史为 V1 已关闭 + V2 已启用", [(v["版本号"], v["status"]) for v in versions] == [("V1", "已关闭"), ("V2", "已启用")])
report_v2 = call("GET", "/api/dashboard/4/report")[1]
check("旧引用仍失效但指向新可用版本 V2", report_v2["当前引用"]["引用状态"] == "失效" and report_v2["当前引用"]["新可用版本"] == "V2")
republish = action("dashboard", 4, {"action": "发布看板", "路径id": 4, "publish_guard": GUARD})
check("重新发布成功", republish["ok"] is True)
report_final = call("GET", "/api/dashboard/4/report")[1]
check("重新发布后引用切换到 V2 且仍只有一份", report_final["当前引用"]["版本号"] == "V2" and report_final["当前引用"]["引用状态"] == "生效")
check("切换后默认展开层仍共享为 2", report_final["当前引用"]["默认展开层"] == 2)

print("== 11. 其余发布守卫阈值（度量上限 5、深度范围 1~8）==")
_, bad1 = call("POST", "/api/datadrill", {"values": {
    "路径编号": "DATA-GUARD-MEASURE", "起始维度": "a", "下钻层级": ["a", "b"],
    "每层维度名": ["a", "b"], "每层度量": [["m1", "m2", "m3", "m4", "m5", "m6"], ["m1", "m2", "m3", "m4", "m5", "m6"]],
    "路径深度": 2, "默认展开层": 1,
}})
check("度量超上限路径登记成功（留待启用守卫拦截）", bad1["ok"] is True, bad1.get("message", ""))
new_id = bad1["entry"]["id"]
res = action("datadrill", new_id, {"action": "启用路径"})
check("每层度量超 5 项被拦截", res["ok"] is False and "每层度量上限" in res["message"])
_, bad2 = call("POST", "/api/datadrill", {"values": {
    "路径编号": "DATA-GUARD-DEPTH", "起始维度": "a", "下钻层级": ["a"],
    "每层维度名": ["a"], "每层度量": [["m1"]],
    "路径深度": 9, "默认展开层": 1,
}})
check("深度越界路径登记成功（留待启用守卫拦截）", bad2["ok"] is True, bad2.get("message", ""))
res2 = action("datadrill", bad2["entry"]["id"], {"action": "启用路径"})
check("路径深度超出 1~8 被拦截", res2["ok"] is False and "路径深度" in res2["message"])

print()
print(f"通过 {_passed} 项，失败 {len(_failures)} 项")
if _failures:
    for item in _failures:
        print(" -", item)
    sys.exit(1)
print("全部验收通过")
