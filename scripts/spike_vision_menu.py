"""SPIKE: Playwright + Claude Opus vision menu extraction.

Goal: prove (or disprove) that we can navigate to a restaurant's
website, screenshot the rendered menu page, and have Claude Opus
return clean structured items with consistent categories — better than
what `scrape_competitor_menu`'s Claude web search currently produces.

If this spike works on 3 known anchors, we swap it in behind the
existing scrape interface. If it produces junk, we walk away — total
sunk time ~30 min.

Test set: The View Pizza at Crestview, Nicky's Pizza, Enfield Pizza.

For each:
  1. Resolve website URL via Place Details (already cached)
  2. Playwright loads the page, light scroll to trigger lazy content,
     full-page screenshot
  3. If the landing page doesn't look like a menu, try to follow a
     "menu"-ish link (best effort, no platform-specific scripts yet)
  4. Send screenshot to Claude Opus vision with a tight extraction
     prompt
  5. Print: item count, distinct categories, first 5 items per cat
"""
import os
import sys
import base64
import json
from datetime import datetime
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
import anthropic

OUT_DIR = REPO / "storage" / "logs" / "spike_screenshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CLAUDE_MODEL = "claude-opus-4-7"
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

ANCHORS = [
    ("ChIJkxtfm1_j5okR4AyKvzW3-jo", "The View Pizza at Crestview"),
    ("ChIJ8T8WTPbj5okRTJc7zjL4K3k", "Nicky's Pizza"),
    ("ChIJwRxSorPk5okRjDCUClAwA9c", "Enfield Pizza"),
]

EXTRACTION_PROMPT = """This is a screenshot of a restaurant's menu page.

Extract every menu item visible. Return a pipe-delimited table — one
item per line, NO header row, NO prose, NO markdown fence:

  category|name|price_cents

Rules:
- Only include items with explicit visible prices.
- Use the EXACT item name as shown (don't invent variants).
- price_cents is integer cents only (e.g., $11.99 → 1199).
- Use the menu's own section/header as the category (e.g.,
  "Specialty Pizzas", "Calzones"). Don't invent your own taxonomy.
- Include size/portion variants as separate rows when each has its
  own price.
- Ignore navigation, footers, hours, address, social buttons.
- If the screenshots show no menu, return nothing (empty output).
"""


def parse_extraction(text: str) -> list[dict]:
    """Parse pipe-delimited items. Tolerate occasional bad lines."""
    out: list[dict] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        cat, name, price = parts[0].strip(), parts[1].strip(), parts[2].strip()
        # price might have $ or .99 — strip and parse
        price_clean = "".join(ch for ch in price if ch.isdigit())
        if not name or not price_clean:
            continue
        out.append({
            "name": name,
            "price_cents": int(price_clean),
            "category": cat,
        })
    return out


def resolve_url(place_id: str, name: str) -> str | None:
    from storage.price_intel import get_place_details
    d = get_place_details(place_id)
    return (d or {}).get("website") if d else None


def find_menu_link(page) -> str | None:
    """Best-effort: find the most menu-like link on the page. Skips
    PDFs (Playwright can't navigate them) and qualifier menus
    ('bereavement menu', 'kids menu', 'lunch specials')."""
    try:
        links = page.eval_on_selector_all(
            "a",
            """els => els.map(a => ({href: a.href || '', text: (a.innerText||'').trim()}))""",
        )
    except Exception:
        return None
    SKIP_TOKENS = ("bereavement", "catering", "kids", "lunch special",
                   "wine list", "drink", "cocktail", "events")
    candidates = []
    for ln in links:
        text = (ln.get("text") or "").lower().strip()
        href = ln.get("href") or ""
        if not href or href.startswith("javascript:"):
            continue
        # Skip download files — Playwright can't render PDFs
        href_lower = href.lower().split("?")[0]
        if href_lower.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")):
            continue
        if "menu" not in text:
            continue
        if any(tok in text for tok in SKIP_TOKENS):
            continue
        # Score: prefer shorter, prefer text that's literally "menu"
        score = (0 if text == "menu" else 1, len(text))
        candidates.append((score, href, text))
    candidates.sort()
    return candidates[0][1] if candidates else None


def shrink_image_if_needed(path: Path, max_bytes: int = 4_500_000,
                            max_dim: int = 7500) -> list[Path]:
    """Anthropic vision rejects > 5MB OR any dimension > 8000 pixels.
    For tall menu screenshots, CHUNK vertically rather than squash —
    squashing destroys readability. Returns a list of paths (one or
    more chunks)."""
    try:
        from PIL import Image
    except ImportError:
        print("  ! Pillow missing — can't resize. Install: pip install pillow")
        return [path]
    img = Image.open(path)
    w, h = img.size

    # Width handling: scale uniformly if too wide (rare)
    if w > max_dim:
        s = max_dim / w
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
        w, h = img.size

    # Height handling: chunk into pieces of max_dim with 150px overlap
    chunks: list[Path] = []
    chunk_h = max_dim - 150
    y = 0
    idx = 0
    while y < h:
        bottom = min(y + max_dim, h)
        crop = img.crop((0, y, w, bottom))
        out = path.with_name(f"{path.stem}_chunk{idx}.jpg")
        # Iteratively reduce JPEG quality if file too big
        for q in (85, 75, 65, 55):
            crop.convert("RGB").save(out, "JPEG", quality=q, optimize=True)
            if out.stat().st_size <= max_bytes:
                break
        chunks.append(out)
        idx += 1
        if bottom >= h:
            break
        y += chunk_h

    if len(chunks) == 1:
        print(f"  → resized {path.name} → {chunks[0].name} "
              f"({chunks[0].stat().st_size/1e6:.2f}MB, {w}x{h})")
    else:
        total_mb = sum(c.stat().st_size for c in chunks) / 1e6
        print(f"  → chunked {path.name} → {len(chunks)} parts, "
              f"{total_mb:.2f}MB total (page {w}x{h})")
    return chunks


def screenshot_menu(url: str, name: str) -> Path | None:
    """Navigate, light scroll, screenshot. Return path or None."""
    safe = "".join(c if c.isalnum() else "_" for c in name)[:40]
    out_path = OUT_DIR / f"{safe}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 1800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            page.set_default_timeout(20000)
            print(f"  → loading {url}")
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as e:
                print(f"  ✗ goto failed: {e}")
                return None
            page.wait_for_timeout(2500)

            # If the landing page isn't already a menu, find the menu
            # page. Try common paths first (works for ~half of mom-and-
            # pop sites), then fall back to scoring links on the page.
            def _is_menu_page(p) -> bool:
                # Real menus have many priced items. A homepage with one
                # promo box has maybe 8-12 dollar signs — not a menu.
                # Threshold 25 separates promo-box from full menu.
                content = (p.content() or "").lower()
                return content.count("$") >= 25

            if not _is_menu_page(page):
                from urllib.parse import urlparse, urlunparse
                base = urlparse(url)
                origin = urlunparse((base.scheme, base.netloc, "", "", "", ""))
                tried = False
                for path in ("/menu", "/our-menu", "/menus", "/food",
                             "/order", "/menu/", "/food-menu",
                             "/our-food", "/dine-in"):
                    candidate = origin + path
                    print(f"  → trying common path: {candidate}")
                    try:
                        page.goto(candidate, wait_until="domcontentloaded",
                                  timeout=10000)
                        page.wait_for_timeout(2000)
                        if _is_menu_page(page):
                            tried = True
                            print(f"  ✓ menu detected at {candidate}")
                            break
                    except Exception:
                        continue
                if not tried:
                    # Reset to landing, try link scoring
                    try:
                        page.goto(url, wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass
                    ml = find_menu_link(page)
                    if ml and ml != url:
                        print(f"  → following menu link: {ml}")
                        try:
                            page.goto(ml, wait_until="domcontentloaded")
                            page.wait_for_timeout(2500)
                        except Exception as e:
                            print(f"  ✗ menu-link goto failed: {e}")

            # Light scroll to trigger lazy-loading
            try:
                page.evaluate(
                    "() => new Promise(r => { let y=0; "
                    "const t=setInterval(() => { window.scrollBy(0, 400); "
                    "y+=400; if(y>4000){clearInterval(t);r()} }, 200) })"
                )
                page.wait_for_timeout(1500)
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_timeout(500)
            except Exception:
                pass

            page.screenshot(path=str(out_path), full_page=True)
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  ✓ screenshot saved: {out_path.name} ({size_mb:.2f} MB)")
            return out_path
        finally:
            browser.close()


def extract_with_claude(image_path: Path) -> list[dict]:
    chunks = shrink_image_if_needed(image_path)
    content: list = []
    for c in chunks:
        media_type = "image/jpeg" if c.suffix.lower() == ".jpg" else "image/png"
        img_b64 = base64.standard_b64encode(c.read_bytes()).decode()
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": img_b64,
            },
        })
    if len(chunks) > 1:
        content.append({
            "type": "text",
            "text": (
                f"There are {len(chunks)} images above — they are sequential "
                "vertical slices of the SAME menu page (top to bottom, with a "
                "small overlap). Extract items from all of them as one menu, "
                "deduping items that appear in the overlap region.\n\n"
                + EXTRACTION_PROMPT
            ),
        })
    else:
        content.append({"type": "text", "text": EXTRACTION_PROMPT})
    print(f"  → Claude vision call ({CLAUDE_MODEL}, {len(chunks)} image(s))")
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
    )
    if not msg.content:
        return []
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    items = parse_extraction(text)
    if not items and text.strip():
        print(f"  ✗ no items parsed; first 300 chars: {text[:300]!r}")
    return items


def summarize(name: str, items: list[dict]):
    print(f"\n  → {len(items)} items extracted")
    if not items:
        return
    cats: dict = {}
    for it in items:
        c = it.get("category") or "?"
        cats.setdefault(c, []).append(it)
    print(f"  → {len(cats)} distinct categories:")
    for c, lst in sorted(cats.items(), key=lambda x: -len(x[1])):
        print(f"     [{len(lst):>3}] {c}")
        for it in lst[:3]:
            price = it.get("price_cents") or 0
            print(f"           {it.get('name',''):40} ${price/100:.2f}")


def main():
    transcript = []
    def log(s):
        print(s)
        transcript.append(s)

    log(f"=== Vision-menu spike — {datetime.now():%Y-%m-%d %H:%M} ===")

    all_results = {}
    for place_id, name in ANCHORS:
        log(f"\n--- {name} ---")
        url = resolve_url(place_id, name)
        if not url:
            log("  ✗ no website resolved")
            continue
        log(f"  url: {url}")
        try:
            shot = screenshot_menu(url, name)
        except Exception as e:
            log(f"  ✗ screenshot failed: {type(e).__name__}: {e}")
            continue
        if not shot:
            continue
        try:
            items = extract_with_claude(shot)
        except Exception as e:
            log(f"  ✗ Claude call failed: {type(e).__name__}: {e}")
            continue
        all_results[name] = items
        summarize(name, items)

    log("\n=== CROSS-ANCHOR CATEGORY OVERLAP ===")
    if len(all_results) >= 2:
        from collections import Counter
        all_cats = Counter()
        for items in all_results.values():
            seen = {it.get("category") for it in items if it.get("category")}
            for c in seen:
                all_cats[c] += 1
        for c, n in all_cats.most_common(15):
            log(f"  [{n}/{len(all_results)} restaurants] {c}")

    out_path = (
        REPO / "storage" / "logs"
        / f"spike_vision_menu_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    out_path.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log(f"\n[saved JSON → {out_path}]")


if __name__ == "__main__":
    main()
