"""Find and click a 'Continue' button on the active screen."""
import sys, os, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

config = yaml.safe_load(open("config.yaml"))
from src.agent.screen import ScreenUnderstandingService
from src.capture.screen import ScreenCapture
from src.agent.computer import ComputerController

cap = ScreenCapture(config.get("capture", {}))
comp = ComputerController()
svc = ScreenUnderstandingService(cap, comp)

result = svc.find_element("Continue")
print(f"Find: ok={result.get('ok')} name={result.get('name')} x={result.get('x')} y={result.get('y')}")

if result.get("ok"):
    click_result = svc.visual_click("Continue")
    print(f"Click: ok={click_result.get('ok')} verified={click_result.get('verified')}")
else:
    print("No 'Continue' button visible on screen.")
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        print(f"Active window: \"{title}\"")
    except Exception:
        pass
    elements = svc.get_elements()
    buttons = [e for e in (elements or []) if e.get("role") == "Button"][:8]
    if buttons:
        print("Visible buttons:")
        for b in buttons:
            print(f"  - \"{b.get('name')}\" at ({b.get('x')}, {b.get('y')})")
    else:
        print("No buttons found on screen either.")
