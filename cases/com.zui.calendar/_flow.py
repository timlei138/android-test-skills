#!/usr/bin/env python3
"""com.zui.calendar 可复用流程（flows）——本 App 多个用例共享，避免各自复制。

组织约定（cases 按被测 App 包名分目录）：
- 每个 App 一个 cases/<包名>/_flow.py；目录名 = 包名，与 knowledge/<包名>.md 同键
- 只放"入口/前置链路"，断言逻辑留给各用例自己写
- 准入规则：≥2 个用例共享的流程才提取到这里，一次性链路留在用例文件里
- 每个函数返回 bool，失败时已自行 record/blocked
- 生成新用例时：先查 knowledge/<包名>.md 的「标准链路」，命中即在这里找现成函数

已有流程：
  goto_课程表空状态(t)          主页 → 更多 → 课程表（保证为空状态）
  goto_图库导入_基本信息确认页(t)  完整图库导入链路 → 确认课程表基本信息页
  goto_手动创建课程表(t)          课程表空状态 → 手动创建页
  tap_more_menu(t)              点顶栏「更多」并确认菜单弹出
  tap_rightmost_icon(t)         顶栏最右图标坐标（识别结果页禁用）
  top_bar_icons(t)              顶栏图标列表（调试辅助）

权限测试（169 系列共享；拆独立用例避免 USER_FIXED 级联）：
  navigate_to_course_table(t)   启动 App + 过首启弹窗 + 到课程表空状态页
  test_camera(t, allow)         相机权限 允许/拒绝 单行为测试
  test_gallery(t, allow)        图库权限 允许/拒绝 单行为测试
"""
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# test_framework 在 <skill包>/framework（本文件在 cases/<包名>/ 下），需显式加入路径
_FW = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework")
if _FW not in sys.path:
    sys.path.insert(0, _FW)

# TestCase 仅作类型提示；流程函数接受任何实现了框架 API 的对象
try:
    from test_framework import TestCase  # noqa: F401
except ImportError:  # 框架不可用时仍允许本模块被导入检查
    TestCase = object

PKG = "com.zui.calendar"

# 图库导入用到的素材与控件
IMG_PATH = "/sdcard/Pictures/日历/课程表.png"
BTN_GALLERY = "com.zui.calendar:id/btnImportFromGallery"
CROP_DONE = "com.zui.calendar:id/btnDone"
BTN_NEXT = "com.zui.calendar:id/btn_next"
PHOTO_THUMB = "com.android.providers.media.module:id/icon_thumbnail"

# 确认课程表基本信息页控件
PAGE_CONFIRM = "确认课程表基本信息"
NAME_RID = "com.zui.calendar:id/et_schedule_name"
BTN_FINISH = "com.zui.calendar:id/btn_finish"
LAY_START = "com.zui.calendar:id/layout_semester_start_date"
TV_START = "com.zui.calendar:id/tv_semester_start_date"
LAY_CUR_WEEK = "com.zui.calendar:id/layout_current_week"
TV_CUR_WEEK = "com.zui.calendar:id/tv_current_week"
LAY_TOTAL = "com.zui.calendar:id/layout_total_weeks"
TV_TOTAL = "com.zui.calendar:id/tv_total_weeks"
SW_WEEKEND = "com.zui.calendar:id/switch_weekend_classes"
SW_NONCURRENT = "com.zui.calendar:id/switch_show_non_current_week"

# 手动创建页（注意：完成按钮是 save_view，不是 btn_finish）
BTN_CREATE_MANUALLY = "com.zui.calendar:id/btnCreateManually"
SAVE_VIEW = "com.zui.calendar:id/save_view"

# 主页顶栏「更多」按钮：无 content-desc，必须用 resource-id 定位
# （靠"最右侧图标"猜测的旧方案已废：不同页面顶栏图标数量不同，会点错）
RID_MORE = "com.zui.calendar:id/iv_more"


def top_bar_icons(t):
    """日历顶栏图标列表 [(cx,cy,rid,desc), ...] 按 x 排序（y∈[100,450] 可点击无文字节点）。
    通用调试辅助；用例里定位具体按钮请优先 tap_rid/tap_text。"""
    items = []
    for n in t.find_nodes(clickable=True):
        b = n.get("bounds_xy")
        if not b or not (100 <= b[1] <= 450):
            continue
        if n["text"]:
            continue
        d = n["desc"]
        if "返回" in d or "上一层级" in d or "back" in d.lower():
            continue   # 排除返回箭头
        items.append(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, n["rid"], d))
    items.sort(key=lambda i: i[0])
    return items


def tap_more_menu(t, retries=8):
    """点主页顶栏「更多」并确认菜单弹出（出现'课程表'项）。
    元素定位（iv_more）+ 重试：等 App 就绪 + 抗转场抖动。成功返回 True。"""
    for _ in range(retries):
        b = t.el_bounds(rid=RID_MORE)
        if b:
            t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            time.sleep(1.2)
            if any("课程表" in x for x in t.screen_text()):
                return True
        time.sleep(1.5)
    return False


def tap_rightmost_icon(t):
    """顶栏最右侧图标（日历语义 = 「更多」）。优先 tap_more_menu()；
    识别结果页等无图标的页面会退化匹配到左侧返回键——不要在这类页面用。"""
    icons = top_bar_icons(t)
    return (icons[-1][0], icons[-1][1]) if icons else None


def _sleep(s=1.0):
    time.sleep(s)


def _tap_rid_raw(t, rid):
    """按 resource-id 取 bounds 点击。
    PhotoPicker 属系统包，用原生 adb input 比 u2 更稳。
    """
    b = t.el_bounds(rid=rid)
    if b:
        t.adb_shell("input", "tap",
                    str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))
        return True
    return False


def restart_calendar(t, pm_clear=True):
    """冷启动日历并过掉首次引导弹窗。pm_clear=True 会清空数据（用例前置）。"""
    if pm_clear:
        t.pm_clear(PKG)
        _sleep(1.2)
    subprocess.run(["adb", "shell", "am", "force-stop", PKG],
                   capture_output=True)
    _sleep(0.8)
    subprocess.run(["adb", "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    time.sleep(4)
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=12, verbose=False)
    _sleep(1.5)
    return True


def goto_课程表空状态(t, pm_clear=True):
    """主页 → 更多 → 课程表，保证停在空状态。返回是否成功。"""
    restart_calendar(t, pm_clear=pm_clear)
    if not tap_more_menu(t):
        t.blocked("无法打开'更多'菜单")
        return False
    _sleep(1.5)
    if not t.tap_text("课程表", wait=4):
        t.blocked("未找到'课程表'入口")
        return False
    _sleep(3)
    if "还未添加课程表" not in " ".join(t.screen_text()):
        t.blocked("课程表非未添加课程表")
        return False
    return True


def goto_手动创建课程表(t, pm_clear=True):
    """课程表空状态 → 手动创建课程表页。"""
    if not goto_课程表空状态(t, pm_clear=pm_clear):
        return False
    if not t.tap_rid(BTN_CREATE_MANUALLY):
        t.blocked("未找到'手动创建课程表'按钮")
        return False
    _sleep(3)
    return True


def goto_图库导入_基本信息确认页(t, pm_clear=True, timeout=40):
    """完整图库导入链路 → 到达「确认课程表基本信息」页。

    对应 knowledge/com.zui.calendar.md 的「标准链路/图库导入创建课程表」。
    步骤：导入入口 → 知道了 → 允许权限 → 照片tab → 选图 → 裁剪完成
          → 等解析 → 下一步
    """
    if not goto_课程表空状态(t, pm_clear=pm_clear):
        return False

    # 图片必须先被 MediaStore 收录，否则 PhotoPicker 显示"无相册"
    t.adb_shell("am", "broadcast", "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", "file:///sdcard/Pictures/日历/课程表.png")
    _sleep(2)

    if not t.tap_rid(BTN_GALLERY):
        t.blocked("未找到'从图库导入课程表'按钮")
        return False
    _sleep(2)
    t.tap_text("知道了", wait=3)
    _sleep(2.5)
    t.tap_text("全部允许", wait=4)      # 系统照片权限
    _sleep(3.5)

    t.tap_text("照片", wait=3)           # PhotoPicker 切到照片 tab
    _sleep(3)
    if not _tap_rid_raw(t, PHOTO_THUMB):
        t.blocked("PhotoPicker 无可选图片（缺素材或未被 MediaStore 收录）")
        return False
    _sleep(5)

    if not _tap_rid_raw(t, CROP_DONE):   # 裁剪页 → 完成
        t.blocked("未进入裁剪页")
        return False

    ok = False
    for _ in range(timeout):
        _sleep(1)
        if "确认识别结果" in " ".join(t.screen_text()):
            ok = True
            break
    if not ok:
        t.blocked(f"图片解析未完成（超时 {timeout}s，需联网）")
        return False
    t.screenshot("确认识别结果")
    _sleep(1)

    if not _tap_rid_raw(t, BTN_NEXT):
        t.blocked("未找到'下一步'")
        return False
    _sleep(3)
    if PAGE_CONFIRM not in " ".join(t.screen_text()):
        t.blocked("未进入'确认课程表基本信息'页")
        return False
    t.screenshot("基本信息确认页")
    return True


# ── 权限测试共享辅助（原 _perm_helper.py，169 系列用例共用）──────────────
# 拆分原因：Android 权限拒绝后 USER_FIXED，同轮多次测会级联失败；
# 每个行为独立干净环境最可靠。


def navigate_to_course_table(t):
    """启动 App + 处理首启弹窗 + 导航到课程表空状态页"""
    # 确保干净前台：杀掉日历和相机（上个用例可能停在相机）
    subprocess.run(["adb", "shell", "am", "force-stop", PKG], capture_output=True)
    subprocess.run(["adb", "shell", "am", "force-stop", "com.zui.camera"],
                   capture_output=True)
    time.sleep(1)
    subprocess.run(["adb", "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    # 等 App 就绪（有弹窗或有主界面工具栏），最多 10s
    for _ in range(10):
        texts = t.screen_text()
        if any(w in " ".join(texts) for w in ("同意", "允许", "我知道了", "知道了")):
            break
        if tap_rightmost_icon(t):
            break
        time.sleep(1)
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=15, verbose=True)
    if not tap_more_menu(t, retries=10):
        print("[导航] 更多菜单未弹出（无'课程表'项）, 屏幕:", t.screen_text()[:6])
        return False
    t.tap_text("课程表")
    for _ in range(10):
        if any("拍照导入" in x for x in t.screen_text()):
            return True
        time.sleep(0.6)
    return False


def _dismiss_image_hint(t, timeout=8):
    """关掉「请确保图片清晰、完整」提示框（点'知道了'）。

    这个框会挡住后续动作：不点掉，相机/相册永远不会被拉起
    （实测点击拍照导入后 25s 相机仍未打开，直至手动关框）。
    看门狗虽会兜底，但存在时序竞争（169 主用例赢过、独立用例输过），
    所以在权限测试里显式等待并关闭，不等看门狗。
    """
    for _ in range(int(timeout / 0.5)):
        texts = t.screen_text()
        if any("知道了" in x for x in texts):
            t.tap_text("知道了", wait=2)
            time.sleep(0.6)
            return True
        # 框没出现（或已消失）→ 可能直接进了系统权限弹窗/相机
        act = t.current_activity()
        if any(k in act.lower() for k in ("camera", "photopicker", "permission")):
            return False
        time.sleep(0.5)
    return False


def test_camera(t, allow):
    """相机权限: allow=True 期望相机打开; allow=False 期望提示授予权限"""
    t.watchdog_policy("allow" if allow else "deny")
    t.tap_text("拍照导入课程表")
    _dismiss_image_hint(t)          # 挡路框必须先关，否则相机不会被拉起
    if allow:
        act = ""
        for _ in range(14):
            time.sleep(0.7)
            act = t.current_activity()
            if "camera" in act.lower():
                break
        ok = "camera" in act.lower() or "zui.camera" in act.lower()
        t.record("PASS" if ok else "FAIL",
                 f"允许后相机打开: {act}")
        t.screenshot("相机_允许")
    else:
        time.sleep(3)
        texts = t.screen_text()
        denied = any("相机权限" in x for x in texts) or any("前往设置" in x for x in texts)
        t.record("PASS" if denied else "FAIL",
                 f"拒绝后提示需要授予相机权限: {texts[:5]}")
        t.screenshot("相机_拒绝")


def test_gallery(t, allow):
    """图库权限: allow=True 期望进入照片选择; allow=False 期望提示授予权限"""
    t.watchdog_policy("allow" if allow else "deny")
    t.tap_text("从图库导入课程表")
    _dismiss_image_hint(t)          # 挡路框必须先关，否则相册不会被拉起
    if allow:
        act = ""
        for _ in range(14):
            time.sleep(0.7)
            act = t.current_activity()
            if "photopicker" in act.lower():
                break
        ok = "photopicker" in act.lower() or "PhotoPicker" in act
        t.record("PASS" if ok else "FAIL",
                 f"允许后进入照片选择界面: {act}")
        t.screenshot("图库_允许")
    else:
        time.sleep(3)
        texts = t.screen_text()
        denied = any("权限" in x for x in texts) and any("前往设置" in x for x in texts)
        t.record("PASS" if denied else "FAIL",
                 f"拒绝后提示需要授予相册权限: {texts[:5]}")
        t.screenshot("图库_拒绝")
