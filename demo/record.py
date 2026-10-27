"""Record a demo video of the vibesequence viewer.

    pip install playwright && playwright install chromium
    vibesequence serve                      # viewer on http://127.0.0.1:8765
    python demo/record.py [base_url]        # -> demo/videos/vibesequence-demo.webm
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
SIZE = {"width": 1920, "height": 1080}
OUT = Path(__file__).parent / "videos"
NAV_W = 380                                # sidebar width in px (viewer default 248 truncates long flow names)

# Playwright videos don't show the mouse: draw a dot that follows it and pulses on click.
CURSOR = """
addEventListener('DOMContentLoaded', () => {
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;z-index:99999;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;'
    + 'background:rgba(35,131,226,.35);border:2px solid #2383E2;pointer-events:none;transition:transform .12s;left:-50px;top:-50px';
  document.body.appendChild(d);
  addEventListener('mousemove', e => { d.style.left = e.clientX + 'px'; d.style.top = e.clientY + 'px'; }, true);
  addEventListener('mousedown', () => d.style.transform = 'scale(.6)', true);
  addEventListener('mouseup', () => d.style.transform = '', true);
});
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE, color_scheme="light")
        ctx.add_init_script(CURSOR)
        # the viewer reads its sidebar width from saved layout prefs before first paint
        ctx.add_init_script(f"localStorage.setItem('vibesequence.layout', JSON.stringify({{navW: {NAV_W}}}))")
        page = ctx.new_page()
        pause = page.wait_for_timeout

        def glide(loc):                     # move the visible cursor smoothly onto an element
            loc.scroll_into_view_if_needed()
            b = loc.bounding_box()
            page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=25)

        def click(loc, wait=900):
            glide(loc)
            pause(250)
            page.mouse.down(); page.mouse.up()
            pause(wait)

        def rendered():
            page.locator("#diagram svg").wait_for()
            pause(1200)

        def key(k, wait=900):
            page.evaluate("document.activeElement && document.activeElement.blur()")   # keys are ignored in inputs
            page.keyboard.press(k)
            pause(wait)

        # 1. open a flow
        page.goto(BASE + "#examples.next_shop.app.api.me.route_GET")
        page.mouse.move(960, 540)
        rendered()
        pause(1500)

        # 2. play it arrow by arrow
        click(page.locator("#playBtn"), wait=0)
        expect(page.locator("#playBtn")).to_contain_text("Replay", timeout=120_000)
        pause(1500)

        # 3. search the sidebar and open another flow
        click(page.locator("#q"), wait=300)
        page.keyboard.type("orders", delay=140)
        pause(800)
        click(page.locator(".flow").filter(has_text=re.compile(r"^POST\s*/api/orders$")))
        rendered()

        # 4. semantic zoom: participant level tabs
        for level in ["module", "package", "function", "class"]:
            click(page.locator(f'#ladder [data-level="{level}"]'), wait=0)
            rendered()

        # 5. depth: hover the (i) to peek, pick depth 3
        glide(page.locator("#depthInfoBtn"))
        pause(1500)
        click(page.locator('#depthInfo [data-depth="3"]'), wait=0)
        rendered()
        key("Escape")

        # 6. click a blue call to zoom into it, then back via the breadcrumb
        click(page.locator("#diagram .messageText.zoomable").first, wait=0)
        rendered()
        glide(page.locator("#crumbs"))
        pause(1200)
        click(page.locator("#crumbs button").nth(1), wait=0)
        rendered()

        # 7. visual zoom + drag to pan
        click(page.locator("#zoomIn"), wait=500)
        click(page.locator("#zoomIn"))
        c = page.locator("#canvas").bounding_box()
        cx, cy = c["x"] + c["width"] / 2, c["y"] + c["height"] / 2
        page.mouse.move(cx, cy, steps=15)
        page.mouse.down()
        page.mouse.move(cx - 350, cy - 250, steps=40)
        page.mouse.up()
        pause(900)
        click(page.locator("#zoomFit"), wait=1200)

        # 8. step through with the arrow keys at 2x, then reset
        click(page.locator("#speedBtn"), wait=400)
        for _ in range(5):
            key("ArrowRight", wait=700)
        key("Escape")

        # 9. zen mode, dark mode, exit zen
        click(page.locator("#zenToggle"), wait=1500)
        key("d", wait=0)
        rendered()
        click(page.locator("#playBtn"), wait=6000)
        key("Escape", wait=500)             # stop/reset the player
        key("Escape", wait=1500)            # exit zen

        video = page.video
        ctx.close()
        browser.close()
        dest = OUT / "vibesequence-demo.webm"
        Path(video.path()).replace(dest)
        print(dest)


if __name__ == "__main__":
    main()
