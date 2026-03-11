from __future__ import annotations


WINDOW_TITLE = "Muenster Bus Board"
WINDOW_WIDTH = 1420
WINDOW_HEIGHT = 920
WINDOW_MIN_SIZE = (980, 700)
WINDOW_BACKGROUND = "#08111f"


def run_desktop_shell(url: str) -> bool:
    try:
        import webview
    except ImportError:
        return False

    try:
        webview.create_window(
            WINDOW_TITLE,
            url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
            text_select=True,
            confirm_close=False,
            background_color=WINDOW_BACKGROUND,
        )
        webview.start(gui="edgechromium", debug=False)
        return True
    except Exception:
        return False
