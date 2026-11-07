# -*- coding: utf-8 -*-
"""微信语音呼叫通道（B-11 caller 注入实现）
================================================

原理：微信 4.x 为 Qt 自绘界面，UIA 控件树封闭（实测 4.1.15 只暴露两个空窗格，
wxauto4 免费版也无法附着），故用「键盘搜索 + 固定几何 + 图像校验 + 模拟点击」：

  1. 激活微信主窗口并固定几何（位置/尺寸固定，坐标才稳定——同 wxauto 的 resize 思路）
  2. Ctrl+F 聚焦搜索 → 剪贴板粘贴联系人名 → Enter 打开聊天
  3. 标题区截图与登记时留存的参考图做 aHash 比对——**防搜错人**（打错人是最危险故障）
  4. 点击聊天右上角「通话」按钮 → 弹出菜单点「语音通话」
  5. 任一步失败返回 False（store 层诚实降级兜底话术，绝不装打通）

使用前提：
  - 桌面版微信 4.1.x 已登录（窗口类名 Qt51514QWindowIcon）
  - pip install uiautomation pywin32 pillow
  - 首次对某联系人呼叫前，须先 calibrate(name) 留存标题参考图（wechat_refs/）

已知限制（如实）：坐标基于固定几何 1196x1115 @ (120,80)；微信大版本更新可能
改变按钮位置，需重新校准。呼叫发起成功 ≠ 对方接听。
"""
import os
import time

WINDOW_CLASS = "Qt51514QWindowIcon"
WINDOW_TITLE = "微信"
FORCE_RECT = (120, 80, 1196, 1115)          # (x, y, w, h)：固定几何
CALL_BTN_XY = (1040, 112)                    # 聊天右上角「通话」按钮（窗口内相对坐标）
MENU_VOICE_XY = (870, 185)                   # 弹出菜单「语音通话」项（窗口内相对坐标）
TITLE_BOX = (440, 80, 760, 145)              # 聊天标题区（窗口内相对坐标，用于防搜错人）
_REF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "wechat_refs")


def _ahash(img):
    """16x16 平均哈希（PIL 即可，不引 opencv）"""
    g = img.convert("L").resize((16, 16))
    px = list(g.getdata())
    avg = sum(px) / len(px)
    return [1 if p > avg else 0 for p in px]


def _hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def _get_window():
    import uiautomation as auto
    w = auto.WindowControl(searchDepth=1, ClassName=WINDOW_CLASS, Name=WINDOW_TITLE)
    return w if w.Exists(3) else None


def _ref_path(name):
    safe = "".join(c for c in name if c not in r'\/:*?"<>|')
    return os.path.join(_REF_DIR, f"{safe}.png")


def calibrate(name):
    """打开与 name 的聊天并留存标题区参考图（呼叫前防搜错人的比对基准）。
    返回 (ok, message)。须由可核对人执行，不得在呼叫时自动生成。"""
    try:
        from PIL import ImageGrab
    except ImportError:
        return False, "缺少 pillow，无法校准"
    if not _open_chat(name):
        return False, "打不开与该联系人的聊天，请确认微信已登录且备注名正确"
    time.sleep(0.8)
    import uiautomation as auto
    r = _get_window().BoundingRectangle
    img = ImageGrab.grab(bbox=(r.left + TITLE_BOX[0], r.top + TITLE_BOX[1],
                               r.left + TITLE_BOX[2], r.top + TITLE_BOX[3]))
    os.makedirs(_REF_DIR, exist_ok=True)
    img.save(_ref_path(name))
    return True, f"已留存 {name} 的标题参考图：{_ref_path(name)}"


def _open_chat(name):
    """搜索并打开与 name 的聊天；失败返回 False"""
    import uiautomation as auto
    w = _get_window()
    if w is None:
        print("[wechat_caller] 未找到微信主窗口（未登录或未启动）")
        return False
    try:
        w.SetActive()
        time.sleep(0.5)
        # 固定几何：位置/尺寸稳定是坐标点击的前提
        x, y, wd, ht = FORCE_RECT
        r = w.BoundingRectangle
        if (r.left, r.top, r.width(), r.height()) != (x, y, wd, ht):
            w.MoveWindow(x, y, wd, ht)
            time.sleep(0.5)
            w.SetActive()
            time.sleep(0.3)
        # Ctrl+F → 粘贴名字 → Enter（中文走剪贴板，比模拟键入可靠）
        import win32clipboard
        for _ in range(3):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(name, win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                break
            except Exception:
                time.sleep(0.3)
        auto.SendKeys("{Ctrl}f", waitTime=0.4)
        auto.SendKeys("{Ctrl}v", waitTime=0.8)
        auto.SendKeys("{Enter}", waitTime=1.2)
        return True
    except Exception as e:
        print(f"[wechat_caller] 打开聊天失败: {e}")
        return False


def _verify_title(name):
    """标题区 aHash 与参考图比对，防搜错人。无参考图 → 拒呼（诚实失败）。"""
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        print("[wechat_caller] 缺少 pillow，无法校验标题")
        return False
    ref = _ref_path(name)
    if not os.path.exists(ref):
        print(f"[wechat_caller] 无 {name} 的标题参考图，请先 calibrate('{name}')")
        return False
    import uiautomation as auto
    r = _get_window().BoundingRectangle
    img = ImageGrab.grab(bbox=(r.left + TITLE_BOX[0], r.top + TITLE_BOX[1],
                               r.left + TITLE_BOX[2], r.top + TITLE_BOX[3]))
    dist = _hamming(_ahash(img), _ahash(Image.open(ref)))
    if dist > 8:
        print(f"[wechat_caller] 标题比对不符（aHash 距离 {dist}），拒绝呼叫以防打错人")
        return False
    return True


def wechat_voice_call(phone, name=None):
    """caller(phone, name) 契约：微信呼叫按**名字**搜索（phone 用于无名字时兜底，
    一般不走到）。返回 True=已拉起呼叫；False=失败（上层诚实降级）。"""
    target = str(name or phone or "").strip()
    if not target:
        print("[wechat_caller] 目标名为空，拒绝呼叫")
        return False
    try:
        import uiautomation as auto
        import pythoncom
        pythoncom.CoInitialize()          # 后台线程调用时需要
    except ImportError as e:
        print(f"[wechat_caller] 依赖缺失: {e}")
        return False
    try:
        if not _open_chat(target):
            return False
        time.sleep(0.8)
        if not _verify_title(target):
            return False
        w = _get_window()
        r = w.BoundingRectangle
        # 点「通话」按钮 → 弹出菜单点「语音通话」
        auto.Click(x=r.left + CALL_BTN_XY[0], y=r.top + CALL_BTN_XY[1], waitTime=0.8)
        auto.Click(x=r.left + MENU_VOICE_XY[0], y=r.top + MENU_VOICE_XY[1], waitTime=1.0)
        print(f"[wechat_caller] 已对 {target} 拉起微信语音呼叫")
        return True
    except Exception as e:
        print(f"[wechat_caller] 呼叫失败: {e}")
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
