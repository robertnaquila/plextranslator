"""Interactive screen-region picker for ``ocr --select-region``.

Opens a translucent fullscreen window; drag a rectangle over the subtitle band
and release to select it (Esc cancels). Uses tkinter, which ships with Python
on Windows/macOS. Covers the primary monitor only.
"""

from __future__ import annotations

from typing import Optional

from .ocr import Region


def pick_region() -> Optional[Region]:
    """Let the user drag-select a region; returns it, or None if cancelled."""
    try:
        import tkinter as tk
    except ImportError as exc:  # pragma: no cover - tkinter missing on some Linuxes
        raise RuntimeError(
            "tkinter is not available - pass --region 'left,top,width,height' "
            "instead (or 'bottom')."
        ) from exc

    result = {}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.configure(background="black")
    root.attributes("-topmost", True)
    root.title("Drag a box over the subtitle area (Esc to cancel)")

    canvas = tk.Canvas(root, cursor="cross", bg="gray20", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        root.winfo_screenwidth() // 2,
        60,
        text="Drag a box over the subtitle area - release to select, Esc to cancel",
        fill="white",
        font=("Segoe UI", 16),
    )
    state = {"x0": 0, "y0": 0, "rect": None}

    def on_press(event):
        state["x0"], state["y0"] = event.x, event.y
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=3
        )

    def on_drag(event):
        if state["rect"] is not None:
            canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

    def on_release(event):
        left = min(state["x0"], event.x)
        top = min(state["y0"], event.y)
        width = abs(event.x - state["x0"])
        height = abs(event.y - state["y0"])
        if width >= 10 and height >= 10:
            result["region"] = Region(left=left, top=top, width=width, height=height)
        root.destroy()

    def on_cancel(_event):
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_cancel)
    root.mainloop()
    return result.get("region")
