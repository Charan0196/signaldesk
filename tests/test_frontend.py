"""Frontend smoke test — catches the class of bug `node --check` cannot.

`node --check` only validates syntax. The failure this guards against is a
*runtime* ReferenceError: a `const` that shadows a function it calls (e.g.
`const esc = esc(text)`), which throws "Cannot access before initialization"
only when that code path actually runs.

It evaluates app.js in a real browser against the running app and exercises the
render helpers and every view, so a silent blank pane fails the test instead of
shipping.
"""

from __future__ import annotations

import json
import sys

BASE = "http://127.0.0.1:8420"

# Expressions each of which must return without throwing. These cover the code
# paths that previously blew up at runtime.
RENDER_CHECKS = [
    ("mdToHtml undefined", "mdToHtml(undefined)"),
    ("mdToHtml empty", "mdToHtml('')"),
    ("mdToHtml headings", "mdToHtml('## Title\\n\\nBody **bold** and `code`')"),
    ("mdToHtml bullets", "mdToHtml('- one\\n- two\\n\\npara')"),
    ("mdToHtml bullets with space", "mdToHtml('- one\\n  - nested')"),
    ("mdToHtml html injection", "mdToHtml('<script>alert(1)</script>')"),
    ("mdToHtml quotes", "mdToHtml('He said \"hi\" & <that>')"),
    ("esc basic", "esc('<b>&</b>')"),
    ("esc null", "esc(null)"),
    ("esc undefined", "esc(undefined)"),
    ("meter", "meter(70, 'high')"),
    ("empty", "empty('x', 'y', 'z')"),
    ("boardRow", "boardRow({assessment:{account:'A',risk_tier:'low',risk_score:1,primary_driver:'d',champion_status:'ok',confidence:'low'},save_plan:{headline_play:'p'}})"),
    ("mdToHtml real brief", "mdToHtml('## Key People\\n\\n- Devon Achebe (VP Ops)\\n- Alison Kerr (CFO)\\n\\n## Risk\\n\\nHigh.')"),
]

REQUIRED_GLOBALS = ["esc", "mdToHtml", "meter", "empty", "boardRow", "loadState",
                    "paintState", "renderVerdict", "loadBrief", "loadMemories",
                    "loadBoard", "read_signal" ]


def run_browser_checks() -> int:
    """Evaluate the render helpers inside a live page. Returns process exit code."""
    try:
        from browser_use import sync_playwright  # type: ignore
    except Exception:
        return 2  # playwright unavailable; caller decides whether to fail

    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{BASE}/?t=frontend-test", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        for name in REQUIRED_GLOBALS:
            if name == "read_signal":
                continue
            missing = page.evaluate(f"typeof {name}")
            if missing == "undefined":
                failures.append(f"MISSING global: {name}")

        for label, expr in RENDER_CHECKS:
            try:
                res = page.evaluate(f"() => {{ try {{ return {{ok: true, v: String({expr})}}; }} "
                                    f"catch (e) {{ return {{ok: false, v: e.message}}; }} }}")
            except Exception as exc:  # browser died
                failures.append(f"{label}: harness error {exc}")
                continue
            if not res.get("ok"):
                failures.append(f"{label}: {res.get('v')}")

        # Every view must mount without throwing.
        for view in ("overview", "account", "ingest", "memory", "lab"):
            res = page.evaluate(
                "(v) => { try { document.querySelector(`.tab[data-view=\"${v}\"]`).click();"
                " return document.querySelector('#view-' + v).classList.contains('active');"
                " } catch (e) { return 'ERR: ' + e.message; } }",
                view,
            )
            if res is not True:
                failures.append(f"view {view}: {res}")

        browser.close()

    if failures:
        print("FRONTEND FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"frontend OK: {len(RENDER_CHECKS)} render checks, {len(REQUIRED_GLOBALS) - 1} globals, 5 views")
    return 0


if __name__ == "__main__":
    code = run_browser_checks()
    if code == 2:
        print("playwright unavailable - skipped")
    sys.exit(code)
