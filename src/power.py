"""クロール中のスリープ抑止（仕様書1章）。電源設定は変更しない。"""
import ctypes, contextlib, sys

ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001

@contextlib.contextmanager
def keep_awake(label=""):
    if sys.platform != "win32":
        yield
        return
    k32 = ctypes.windll.kernel32
    ok = k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    if not ok:
        print(f"[power] SetThreadExecutionState 失敗（続行）")
    try:
        yield
    finally:
        k32.SetThreadExecutionState(ES_CONTINUOUS)
