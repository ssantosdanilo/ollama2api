#!/usr/bin/env python3
"""
批量扫描脚本 — 逐段调用 Ollama2API 扫描接口，自动发现 Ollama 节点。

用法:
  python3 batch_scan.py                                      # 默认读取 scan_ranges.json
  python3 batch_scan.py my_ranges.json                       # 指定范围文件
  nohup python3 -u batch_scan.py > scan.log 2>&1 &           # 后台运行

环境变量:
  OLLAMA2API_URL   — 服务地址（默认 http://localhost:8001）
  ADMIN_USERNAME   — 管理员账户（默认 admin）
  ADMIN_PASSWORD   — 管理员密码（必须设置）

范围文件格式 (JSON):
  [{"name": "段名", "start": "1.2.0.0", "end": "1.2.255.255", "force": false}]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("OLLAMA2API_URL", "http://localhost:8001")
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def api_call(path, method="GET", data=None, token=None):
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req_data = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def login():
    if not ADMIN_PASS:
        log("错误: 请设置环境变量 ADMIN_PASSWORD")
        sys.exit(1)
    result = api_call("/admin/api/login", method="POST",
                      data={"username": ADMIN_USER, "password": ADMIN_PASS})
    if "token" in result:
        return result["token"]
    log(f"登录失败: {result}")
    sys.exit(1)


def get_backend_count():
    try:
        req = urllib.request.Request(f"{BASE}/health")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            b = data.get("backends", {})
            return b.get("total", 0), b.get("online", 0)
    except Exception:
        return -1, -1


def trigger_scan(token, start, end, force=False):
    return api_call("/admin/api/scanner/scan-range", method="POST",
                    data={"start": start, "end": end, "force": force}, token=token)


def wait_for_scan(token, name, poll=30):
    while True:
        time.sleep(poll)
        prog = api_call("/admin/api/scanner/progress", token=token)
        if "error" in prog:
            log(f"  [{name}] 进度查询失败: {prog['error']}")
            return None
        p = prog.get("progress", {})
        total, scanned, found = p.get("total", 0), p.get("scanned", 0), p.get("found", 0)
        if total > 0:
            log(f"  [{name}] {scanned}/{total} ({scanned/total*100:.1f}%), 发现: {found}")
        if not prog.get("scanning", False):
            return {"found": found, "total": total}


def load_ranges(path):
    if not os.path.exists(path):
        log(f"错误: 范围文件不存在: {path}")
        log("请创建范围文件，格式参考 scan_ranges.example.json")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        ranges = json.load(f)
    log(f"加载 {len(ranges)} 个扫描范围: {path}")
    return ranges


def main():
    ranges_file = sys.argv[1] if len(sys.argv) > 1 else "scan_ranges.json"
    ranges = load_ranges(ranges_file)

    log("=" * 60)
    log(f"批量扫描启动 — 共 {len(ranges)} 个 IP 段")
    log("=" * 60)

    token = login()
    log("登录成功")
    total_before, online_before = get_backend_count()
    log(f"当前后端: total={total_before}, online={online_before}")

    total_found, completed, failed = 0, 0, 0

    for i, r in enumerate(ranges, 1):
        name = r.get("name", f"{r['start']}-{r['end']}")
        force = r.get("force", False)
        log(f"\n--- [{i}/{len(ranges)}] {name}: {r['start']}-{r['end']} force={force} ---")

        result = trigger_scan(token, r["start"], r["end"], force)
        if "Unauthorized" in str(result.get("error", "")):
            log("  Token 过期，重新登录..."); token = login()
            result = trigger_scan(token, r["start"], r["end"], force)

        if not result.get("success"):
            err = str(result.get("error") or result.get("message", ""))
            if "正在进行中" in err:
                log("  等待当前扫描完成...")
                wait_for_scan(token, "等待")
                result = trigger_scan(token, r["start"], r["end"], force)
                if not result.get("success"):
                    failed += 1; continue
            elif "已扫描过" in err:
                log("  已扫描过，跳过"); continue
            else:
                log(f"  失败: {err}"); failed += 1; continue

        time.sleep(5)
        scan_result = wait_for_scan(token, name)
        if scan_result is None:
            token = login(); scan_result = wait_for_scan(token, name)

        if scan_result:
            total_found += scan_result.get("found", 0); completed += 1
            log(f"  完成! 发现 {scan_result['found']} 个节点")
        else:
            failed += 1; log(f"  异常")

        t, o = get_backend_count()
        log(f"  总进度: {completed}/{len(ranges)}, 发现 {total_found}, 后端 {t}({o} online)")

    log("\n" + "=" * 60)
    total_final, online_final = get_backend_count()
    log(f"扫描完成! 成功 {completed}, 失败 {failed}, 跳过 {len(ranges)-completed-failed}")
    log(f"发现节点: {total_found}, 后端: {total_before}->{total_final} (+{max(0,total_final-total_before)})")
    log("=" * 60)


if __name__ == "__main__":
    main()
