"""Record a short before/after clip of live mode: the code changes while the viewer is open.

    python demo/record_changes.py            # phone: portrait 4:5, 1080x1350 (Instagram / Threads feed)
    python demo/record_changes.py desktop    # desktop: full viewer, 1920x1080
    python demo/record_changes.py desktop --no-captions   # clean: no caption overlay (landing page)
    # -> demo/videos/vibesequence-changes[-desktop].mp4, changes-before/after[-desktop].png  (needs ffmpeg)

Works on a temp copy of examples/shop_api (the repo is not touched): starts `vibesequence serve`, opens
POST /orders zoomed in on the end of OrderService.place, then edits the code so an email receipt replaces
the "order.placed" event. The viewer rebuilds by itself, draws the new step green, lists the removed one,
and plays only the change. Frames come from Chrome's screencast at device pixels (Playwright's own video
recorder ignores the device scale), so text stays sharp.
"""
import base64
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from record import CURSOR                   # the visible cursor dot from record.py

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).parent / "videos"
PORT = 8767

CAPTION_CSS = """
#demo-caption { position: fixed; z-index: 99998; padding: 12px 18px 14px; border-radius: 14px; background: #191919;
  color: #fff; text-align: center; box-shadow: 0 8px 24px rgba(0,0,0,.28); font: 600 20px/1.3 -apple-system,
  BlinkMacSystemFont, sans-serif; }
#demo-caption small { display: block; margin-bottom: 4px; font-size: 13px; letter-spacing: .08em; color: #8fd19e; }
"""
FORMATS = {
    # phone: only the title, the change banner and the diagram; package level so the lanes stay big
    "phone": dict(view={"width": 540, "height": 675}, scale=2, level="package", zoom=1.15, suffix="",
                  flow="app.api.orders_create_order", depth=2,
                  prefs="{navCollapsed: true}", css=CAPTION_CSS + """
.topbar, .props, .lede, .views, .legend, .zoombar, .player { display: none !important; }
.page { padding: 18px 18px 0 !important; }
.doc-head h1 { font-size: 26px !important; margin-bottom: 10px !important; }
.changes { font-size: 14px !important; }
#demo-caption { left: 14px; right: 14px; bottom: 16px; }
"""),
    # desktop: the whole viewer (sidebar, properties, banner, player); captions float at the top.
    # Zoomed into OrderService.place at depth 1, so both ends of the changed arrow fit the screen
    "desktop": dict(view={"width": 1280, "height": 720}, scale=1.5, level="class", zoom="fit", suffix="-desktop",
                    flow="app.services.order_service_OrderService.place", depth=1,
                    prefs="{navW: 300}", css=CAPTION_CSS + """
#demo-caption { top: 10px; left: 50%; transform: translateX(-50%); max-width: 760px; padding: 9px 22px 11px; font-size: 18px; }
#demo-caption small { font-size: 12px; margin-bottom: 2px; }
"""),
}

EMAIL = '''import requests
from app.infra import log


def send_receipt(user, order):
    log.info("sending receipt")
    requests.post("https://mail.example/send", json={"to": user.email, "order": order.id})
'''
BEFORE = '        events.publish("order.placed", order.id)\n'
AFTER = '        email.send_receipt(user, order)\n'

CAPTION = """([kicker, text]) => { let c = document.getElementById('demo-caption');
  if (!c) { c = document.createElement('div'); c.id = 'demo-caption'; document.body.appendChild(c); }
  c.replaceChildren(); const k = document.createElement('small'); k.textContent = kicker; c.append(k, text); }"""
# scroll so the arrow labelled `label` sits centred, a bit above the middle
FOCUS = """label => { const c = document.querySelector('#canvas'), v = c.getBoundingClientRect();
  const t = [...document.querySelectorAll('#diagram .messageText')].find(e => e.textContent.trim().startsWith(label));
  if (!t) return false;
  const r = t.getBoundingClientRect();
  c.scrollBy({ left: r.left + r.width / 2 - (v.left + v.width / 2), top: r.top - (v.top + v.height * 0.45),
               behavior: 'smooth' });
  return true; }"""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fmt = dict(FORMATS[args[0] if args else "phone"], captions="--no-captions" not in sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp()) / "shop_api"
    shutil.copytree(ROOT / "examples" / "shop_api", work)
    server = subprocess.Popen([sys.executable, "-m", "vibesequence", "serve", "--port", str(PORT)], cwd=work,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{PORT}/"
        for _ in range(60):                     # first build, then the server answers
            try:
                urllib.request.urlopen(base, timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        frames, end = record(base, work, fmt)
    finally:
        server.terminate()
        shutil.rmtree(work.parent, ignore_errors=True)
    mp4 = OUT / f"vibesequence-changes{fmt['suffix']}.mp4"
    write_mp4(frames, end, mp4, frame_size(fmt))
    print(mp4)


def frame_size(fmt: dict) -> dict:
    return {k: round(v * fmt["scale"]) for k, v in fmt["view"].items()}


class Screencast:
    """Chrome's screencast: a frame on every repaint, at device pixels, with a timestamp."""

    def __init__(self, page, size: dict):
        self.cdp = page.context.new_cdp_session(page)
        self.frames: list[tuple[float, bytes]] = []
        self.cdp.on("Page.screencastFrame", self._frame)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92,
                                               "maxWidth": size["width"], "maxHeight": size["height"]})

    def _frame(self, ev):
        self.frames.append((ev["metadata"]["timestamp"], base64.b64decode(ev["data"])))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    def stop(self) -> tuple[list[tuple[float, bytes]], float]:
        self.cdp.send("Page.stopScreencast")
        return self.frames, time.time()


def write_mp4(frames: list[tuple[float, bytes]], end: float, dest: Path, size: dict):
    """Frames arrive only when the page repaints: each one is held until the next (ffmpeg concat durations),
    then resampled to a constant 30 fps H.264 that Instagram, Threads and LinkedIn accept."""
    with tempfile.TemporaryDirectory() as d:
        lines = []
        for i, (ts, data) in enumerate(frames):
            f = Path(d) / f"{i:05d}.jpg"
            f.write_bytes(data)
            nxt = frames[i + 1][0] if i + 1 < len(frames) else end
            lines += [f"file '{f}'", f"duration {max(nxt - ts, 0.001):.4f}"]
        lines.append(lines[-2])                 # concat demuxer: repeat the last file so its duration counts
        (Path(d) / "list.txt").write_text("\n".join(lines) + "\n")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(Path(d) / "list.txt"),
                        "-vf", f"fps=30,scale={size['width']}:{size['height']}:flags=lanczos,format=yuv420p",
                        "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-movflags", "+faststart", "-an", str(dest)],
                       check=True)


def record(base: str, work: Path, fmt: dict) -> tuple[list[tuple[float, bytes]], float]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=fmt["view"], device_scale_factor=fmt["scale"], color_scheme="light")
        ctx.add_init_script(CURSOR)
        ctx.add_init_script(f"localStorage.setItem('vibesequence.layout', JSON.stringify({fmt['prefs']}))")
        page = ctx.new_page()
        pause = page.wait_for_timeout
        caption = lambda kicker, text: fmt["captions"] and page.evaluate(CAPTION, [kicker, text])   # noqa: E731
        shot = lambda name: page.screenshot(path=str(OUT / f"changes-{name}{fmt['suffix']}.png"))   # noqa: E731

        def focus(label):
            page.wait_for_function(f"!!document.querySelector('#diagram svg') && ({FOCUS})({label!r})", timeout=20_000)
            pause(1300)

        # 1. before: the end of OrderService.place
        page.goto(base + "#" + fmt["flow"])
        page.locator("#diagram svg").wait_for()
        page.add_style_tag(content=fmt["css"])
        page.locator(f'#ladder [data-level="{fmt["level"]}"]').evaluate("b => b.click()")
        page.wait_for_timeout(700)
        page.locator("#depth").evaluate("(i, d) => { i.value = d; i.dispatchEvent(new Event('change')) }", fmt["depth"])
        page.wait_for_timeout(700)
        page.evaluate("document.getElementById('zoomFit').click()" if fmt["zoom"] == "fit" else f"zoomTo({fmt['zoom']})")
        cast = Screencast(page, frame_size(fmt))   # frames come on repaint: start before the caption and scroll
        caption("BEFORE", "After an order is paid, the app publishes an event")
        focus("publish")
        pause(2500)
        shot("before")

        # 2. the code changes, as an AI assistant would edit it
        caption("THE AI EDITS THE CODE", "“Send an email receipt instead of the event”")
        pause(1800)
        (work / "app" / "infra" / "email.py").write_text(EMAIL)
        svc = work / "app" / "services" / "order_service.py"
        src = svc.read_text()
        assert BEFORE in src, "examples/shop_api changed: update BEFORE in record_changes.py"
        svc.write_text(src.replace("from app.infra import events, log", "from app.infra import email, events, log")
                       .replace(BEFORE, AFTER))

        # 3. after: the viewer rebuilds by itself (it polls every 1.5 s)
        page.locator("#changes").wait_for(state="visible", timeout=20_000)
        caption("AFTER", "The diagram updates itself: the new step is green")
        focus("send_receipt")
        pause(2800)
        shot("after")

        # 4. play only what changed (click the banner's button with the visible cursor)
        caption("REVIEW", "Removed steps are listed. Play just what changed")
        b = page.locator("#playChanges").bounding_box()
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=30)
        pause(300)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_function("!document.querySelector('#playBtn').textContent.includes('Pause')", timeout=30_000)
        pause(2200)

        # 5. end card
        caption("VIBESEQUENCE", "See what the AI changed: pip install vibesequence")
        pause(3000)

        result = cast.stop()
        browser.close()
        return result


if __name__ == "__main__":
    main()
