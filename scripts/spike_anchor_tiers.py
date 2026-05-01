"""Probe every anchor in the Pasquale's competitor list, detect the
hosting platform (Slice, Toast, Square Online, etc.), and classify
each into a quality tier.

Tier A: known SaaS ordering platform → VLM extraction will work great
Tier B: custom site with a visible menu page → VLM works, may need link hunt
Tier C: no menu visible (country club, brochure site, dead URL)

Output: a table per anchor + tier counts. Tells us how many of our 25
neighbors are reliable data sources before we commit to building the
full pipeline.
"""
import os
import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.sync_api import sync_playwright

# Each entry: (platform_name, [tokens to match against page HTML])
PLATFORMS = [
    ("Slice",         ["slicelife.com", "mypizza-assets", "powered by slice"]),
    ("Toast",         ["toasttab.com", "toaststatic.com"]),
    ("Square Online", ["square.site", "squareup.com/online", "weeblycloud"]),
    ("ChowNow",       ["chownow.com", "cn-images"]),
    ("Clover",        ["clover.com/online", "clover-cdn"]),
    ("Beyond Menu",   ["beyondmenu.com"]),
    ("MenuStar",      ["menustar.us", "menustar.com"]),
    ("HungerRush",    ["hungerrush.com", "ordering.hungerrush"]),
    ("GloriaFood",    ["gloriafood.com", "addmenu.com"]),
    ("Popmenu",       ["popmenu.com"]),
    ("BentoBox",      ["getbento.com", "bentobox.cdn"]),
    ("Olo",           ["olo.com", "olocdn"]),
    ("Foodtec",       ["foodtecsolutions.com"]),
    ("Restaurant.com",["restaurant.com"]),
    ("DoorDash",      ["doordash.com/store", "doordash.com/menu"]),
    ("Grubhub",       ["grubhub.com/restaurant"]),
    ("Wix Restaurants",["wix-restaurants", "wixstatic.com/restaurants"]),
    ("MenuFy",        ["menufy.com"]),
    ("Restaurantji",  ["restaurantji.com"]),
    ("Untappd",       ["untappd.com"]),
]

PROBE_JS = """
() => {
    const html = document.documentElement.outerHTML.toLowerCase();
    const text = (document.body.innerText || '').toLowerCase();
    const scripts = Array.from(document.querySelectorAll('script[src]'))
        .map(s => (s.src || '').toLowerCase()).join('\\n');
    const haystack = html + '\\n' + scripts;
    // Count visible $ signs as a menu signal
    const priceCount = (text.match(/\\$\\d+/g) || []).length;
    // Footer text
    const idx = text.indexOf('powered by');
    const poweredBy = idx >= 0 ? text.substr(idx, 80) : '';
    return {
        priceCount,
        title: document.title,
        haystack,
        poweredBy,
    };
}
"""


def detect_platform(haystack: str, powered_by: str) -> str | None:
    blob = haystack + " " + powered_by
    for name, tokens in PLATFORMS:
        for tok in tokens:
            if tok in blob:
                return name
    return None


def probe_anchor(name: str, url: str, idx: int, total: int) -> dict:
    """Headless Playwright load + fingerprint extraction."""
    print(f"[{idx}/{total}] {name[:35]:35} → {url[:60]}", flush=True)
    out = {
        "name": name, "url": url, "platform": None,
        "tier": "?", "price_count": 0, "title": "",
        "powered_by": "", "error": None,
    }
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                ctx = b.new_context(
                    viewport={"width": 1280, "height": 1200},
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0.0.0 Safari/537.36"),
                )
                page = ctx.new_page()
                page.set_default_timeout(15000)
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)
                info = page.evaluate(PROBE_JS)
                out["price_count"] = info["priceCount"]
                out["title"] = info["title"][:80]
                out["powered_by"] = info["poweredBy"][:80]
                out["platform"] = detect_platform(
                    info["haystack"], info["poweredBy"],
                )
            finally:
                b.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:100]}"
        return out

    # Tier classification
    if out["platform"]:
        out["tier"] = "A"
    elif out["price_count"] >= 25:
        out["tier"] = "B"
    elif out["price_count"] >= 5:
        out["tier"] = "B?"
    else:
        out["tier"] = "C"
    return out


def main():
    from storage.price_intel import get_cached_comparisons, get_place_details

    comps = get_cached_comparisons(25)  # restaurant_id=25 = Pasquale's
    print(f"=== {len(comps)} anchors for restaurant 25 ===\n")

    # Resolve URLs in parallel (Place Details is cached, fast)
    anchors_with_url = []
    for c in comps:
        pid = c.get("place_id")
        if not pid:
            continue
        d = get_place_details(pid) or {}
        url = d.get("website")
        if url:
            anchors_with_url.append((c["place_name"], url))
        else:
            print(f"  [skip] {c['place_name']}: no website on file")

    print(f"\n=== probing {len(anchors_with_url)} URLs ===\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(probe_anchor, n, u, i + 1, len(anchors_with_url)): (n, u)
            for i, (n, u) in enumerate(anchors_with_url)
        }
        for fut in as_completed(futs):
            results.append(fut.result())

    # Sort by tier then name
    tier_order = {"A": 0, "B": 1, "B?": 2, "C": 3, "?": 4}
    results.sort(key=lambda r: (tier_order.get(r["tier"], 9), r["name"]))

    print("\n" + "=" * 95)
    print(f"{'TIER':5} {'PLATFORM':18} {'$':>4}  {'NAME':35} {'URL'}")
    print("=" * 95)
    for r in results:
        platform = r["platform"] or ("custom" if r["tier"] in ("B", "B?")
                                      else "—")
        err = f"  ERR: {r['error']}" if r.get("error") else ""
        print(f"{r['tier']:5} {platform:18} {r['price_count']:>4}  "
              f"{r['name'][:35]:35} {r['url'][:50]}{err}")

    # Summary
    counts: dict = {}
    for r in results:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    print("\n=== TIER BREAKDOWN ===")
    for t in ("A", "B", "B?", "C", "?"):
        n = counts.get(t, 0)
        if n:
            label = {
                "A": "known SaaS platform — VLM works reliably",
                "B": "custom site, menu visible — VLM should work",
                "B?": "custom site, sparse prices — uncertain",
                "C": "no menu visible — skip",
                "?": "errored",
            }[t]
            print(f"  Tier {t}: {n:>2}  ({label})")
    print(f"\nA + B = {counts.get('A',0) + counts.get('B',0)} reliable "
          f"anchors out of {len(results)}")

    out_path = REPO / "storage" / "logs" / "anchor_tiers.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n[saved → {out_path}]")


if __name__ == "__main__":
    main()
