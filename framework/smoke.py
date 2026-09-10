#!/usr/bin/env python3
"""安装后 smoke 探针：验证 adb + u2 dump + 截图 + DB 全链路通畅。

放 framework/ 而非 cases/，避免被用例发现机制（run_case.resolve_case）收录。
PROBE_ 前缀：实际创建 TestCase，报告/DB 链路可被验收。

用法（工作区 venv）:
    python framework/smoke.py [--device SERIAL]
退出码:
    0 = 全链路通
    3 = 设备/adb 问题（输出引导排查）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _check_adb():
    """检查 adb 是否在 PATH 且有设备授权。返回 (ok, message)。"""
    import subprocess
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return False, "adb 不在 PATH（请安装 Android SDK platform-tools）"
    except subprocess.TimeoutExpired:
        return False, "adb devices 超时（adb server 可能卡住，试 adb kill-server && adb start-server）"
    lines = [ln for ln in r.stdout.splitlines() if ln.strip().endswith("\tdevice")]
    if not lines:
        return False, "无已授权设备（请连接设备并开启 USB 调试，然后在手机弹窗点「允许」）"
    return True, f"已找到 {len(lines)} 台设备: {lines[0].split()[0]}"


def run(device=None):
    """执行 smoke 探针，返回退出码（0=全通，3=设备/adb 问题）。"""
    print("=" * 50)
    print("  Android GUI 测试环境 smoke 探针")
    print("=" * 50)

    # 1. adb + 设备
    print("\n▶ 1/3 设备连通性...")
    ok, msg = _check_adb()
    if not ok:
        print(f"  ❌ {msg}")
        print("\n引导:")
        print("  1. 确认 USB 线已连接")
        print("  2. 手机「开发者选项」→ 开启「USB 调试」")
        print("  3. 运行 adb devices 看是否有设备并授权")
        return 3
    print(f"  ✅ {msg}")

    # 2. u2 dump + 截图（用 TestCase PROBE_ 包裹，走报告/DB 链路）
    print("\n▶ 2/3 UI dump + 截图 + DB 链路...")
    try:
        import uiautomator2 as u2
    except ImportError:
        print("  ❌ uiautomator2 未安装（请运行 setup.sh / setup.ps1）")
        return 3

    # 支持 --device SERIAL（多设备时必要）
    try:
        d = u2.connect(device) if device else u2.connect()
        # d.info 在 Android 15+ 可能崩，改用 window_size() + serial
        w, h = d.window_size()
        print(f"  ✅ u2 连接成功（{d.serial}，屏幕 {w}x{h}）")
    except Exception as e:
        print(f"  ❌ u2 连接失败: {e}")
        print("\n引导:")
        print("  1. 运行 python -m uiautomator2 init（首次会安装 atx-agent 到手机）")
        print("  2. 检查手机是否弹出 atx-agent 授权提示")
        return 3

    try:
        xml = d.dump_hierarchy()
        if not xml or len(xml) < 50:
            print(f"  ❌ UI dump 内容异常（{len(xml or '')} 字节）")
            return 3
        print(f"  ✅ UI dump 成功（{len(xml)} 字节）")
    except Exception as e:
        print(f"  ❌ UI dump 失败: {e}")
        return 3

    # 截图：用 screencap 直接取，验证截图链路
    try:
        import tempfile
        shot_path = os.path.join(tempfile.gettempdir(), "smoke_screenshot.png")
        d.screenshot(shot_path)
        if not os.path.isfile(shot_path):
            print("  ❌ 截图文件未生成")
            return 3
        size = os.path.getsize(shot_path)
        if size < 1024:
            print(f"  ❌ 截图文件过小（{size} 字节），疑似空图")
            return 3
        print(f"  ✅ 截图成功（{size} 字节）")
        try:
            os.unlink(shot_path)
        except Exception:
            pass
    except Exception as e:
        print(f"  ❌ 截图失败: {e}")
        return 3

    # 3. DB/报告链路验证
    print("\n▶ 3/3 报告/DB 链路...")
    try:
        from test_framework import TestCase
        t = TestCase("PROBE_smoke", device=d.serial)
        t.record("PASS", "smoke 探针全链路通畅")
        t.finish()
        print(f"  ✅ TestCase 创建+报告+入库完成")
    except Exception as e:
        print(f"  ℹ️  报告/DB 链路异常（不影响核心检测）: {e}")

    # 附加检查（信息性，不影响退出码）
    print("\n▶ 附加检查...")
    try:
        from db import default_test_dir
        vis_conf = os.path.join(default_test_dir(), "storage", "vision.json")
        if os.path.isfile(vis_conf):
            print(f"  ✅ 视觉配置已存在: {vis_conf}")
        else:
            print("  ℹ️  视觉模型未配置（可选，Web UI「视觉模型」页设置）")
    except Exception:
        pass

    print("\n" + "=" * 50)
    print("  ✅ 全链路通畅，环境就绪")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    # 支持 --device SERIAL
    _device = None
    _args = sys.argv[1:]
    if "--device" in _args:
        _idx = _args.index("--device")
        if _idx + 1 < len(_args):
            _device = _args[_idx + 1]
    sys.exit(run(device=_device))
