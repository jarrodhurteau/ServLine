# ServLine Implementation Specification
# FINAL — For Claude Code in VS Code

---

## What ServLine Is

ServLine is a menu digitization engine. It takes a photo of a physical restaurant menu, extracts every item through OCR, then runs a second AI verification pass using the Claude API to compare the parse against the original image and fix any errors through process of elimination logic. The result is clean, verified, structured menu data.

ServLine has two completely separate customer experiences that share the same backend engine.

---

## Experience 1: POS Company API (White-Label Engine)

**Who this is for:** POS companies that integrate ServLine into their own onboarding flow.

**Core principle:** The restaurant owner NEVER sees ServLine. No ServLine branding. No ServLine editor. No third-party UI of any kind. The restaurant owner thinks this is a built-in feature of their POS system.

### The Flow

1. Restaurant owner signs up for a new POS system and reaches the menu setup step
2. The POS onboarding flow offers: "Upload a photo of your existing menu"
3. Restaurant uploads a photo (JPEG, PNG, PDF)
4. The POS system sends the image to ServLine's API endpoint
5. **Layer 1 — OCR Extraction:** ServLine's OCR pipeline processes the image and extracts all text, organizing it into structured data (item names, descriptions, prices, categories, modifiers)
6. **Layer 2 — Claude API Verification:** The original menu image AND the OCR output are both sent to the Claude API. Claude independently reads the menu image and compares every line item against the OCR output. It fixes misspellings, corrects wrong prices, adds missing items through process of elimination, removes duplicates, and fixes category assignments. (See "The Dual-Layer AI Pipeline" section below for full details.)
7. **Confidence Gate:** The system calculates an internal confidence score. If below 90%, the API returns a response telling the POS system to prompt for a clearer photo. If 90% or above, proceed.
8. ServLine returns verified structured JSON to the POS system
9. The POS system populates its own native menu editor with the data
10. The restaurant owner sees their POS editor already filled in with their complete menu
11. They review in the POS editor (the one they will use forever), make any minor tweaks, and confirm

### What the API Receives

- Menu image(s) — supports multi-page menus
- POS partner authentication credentials
- Optional: POS partner's preferred data schema

### What the API Returns on Success

```json
{
  "status": "success",
  "confidence_score": 0.94,
  "menu": {
    "categories": [
      {
        "name": "Appetizers",
        "items": [
          {
            "name": "Mozzarella Sticks",
            "description": "Hand-breaded and served with marinara sauce",
            "price": 9.99,
            "modifiers": []
          },
          {
            "name": "Buffalo Wings",
            "description": "Tossed in your choice of sauce. Served with celery and blue cheese",
            "price": 13.99,
            "modifiers": [
              {
                "name": "Sauce Choice",
                "options": ["Mild", "Medium", "Hot", "BBQ"],
                "required": true
              }
            ]
          }
        ]
      }
    ],
    "total_items": 47,
    "total_categories": 6
  }
}
```

### What the API Returns on Low Confidence

```json
{
  "status": "low_confidence",
  "confidence_score": 0.68,
  "message": "Image quality is insufficient for accurate parsing. Please provide a clearer photo with better lighting and minimal glare.",
  "issues": ["blurry_text", "poor_lighting", "glare_detected"]
}
```

### Rules for This Experience

- ServLine's editor is NEVER used. The POS company's own editor handles all review.
- ServLine branding is NEVER visible to the restaurant owner.
- The API is stateless — each request is independent. No sessions, no saved menus.
- The confidence score is NEVER shown to the restaurant owner. They either get clean data or get asked for a better photo.
- Response time target: under 60 seconds for a standard single-page menu.

---

## Experience 2: Direct-to-Restaurant Website (servline.com)

**Who this is for:** Individual restaurant owners who visit the ServLine website directly.

**Core principle:** Two tiers — a genuinely useful free tier and a $50 paid tier for AI-powered parsing. The free tier is not a crippled version of the paid tier. It is a different product.

### Free Account

The free account gives every user access to ServLine's manual menu editor. This is a real, fully functional tool.

**What free users can do:**
- Create an account on servline.com
- Manually type their menu into the ServLine editor (item names, descriptions, prices, categories, modifiers)
- Organize items by category
- Save their menu to their account
- Return anytime to edit, add, or remove items
- Export their menu in any available format:
  - CSV
  - JSON
  - Direct export to their POS system (Square, Toast, Clover)

**What free users CANNOT do:**
- Upload a photo for automated AI-powered parsing. That is the paid feature.

**What this costs ServLine:** Nothing. No OCR processing, no Claude API calls. The free tier is just a web-based editor with a database. No AI costs are incurred.

### Paid Tier — $50 One-Time

For $50, the restaurant owner gets the full AI-powered experience.

**What paid users get:**
- Upload a photo of their menu
- ServLine processes it through the full dual-layer AI pipeline (OCR + Claude verification)
- If confidence is below 90%, prompt for a clearer photo
- If confidence is 90% or above, the verified menu data loads into ServLine's editor
- The restaurant owner can review, edit, add, remove, and reorganize in the editor
- Their menu saves to their account permanently
- They can export in any available format:
  - CSV
  - JSON
  - Direct export to their POS system (Square, Toast, Clover)
- They can return anytime to edit their saved menu and re-export

**There is NO monthly subscription tier at this time.** The $50 is a one-time payment per parse. If a restaurant wants to parse a new menu later (seasonal change, major overhaul), they pay $50 again.

### Rules for This Experience

- ServLine's editor IS the primary interface. This is the full branded ServLine experience.
- Menus are persistent — users have accounts and saved menus.
- The editor supports ongoing manual management: add items, remove items, update prices, reorganize categories.
- Export options must include direct POS integration (Square, Toast, Clover) plus universal CSV/JSON.
- The free manual editor is genuinely useful on its own — not a teaser, not a crippled demo.
- The quality of the AI parse is identical whether the user pays $50 on the website or the POS company sends it through the API. Same pipeline, same accuracy, same dual-layer verification.

---

## The Dual-Layer AI Pipeline

This is the core engine. Both experiences use this exact same pipeline for AI-powered parsing. The free tier does NOT use this pipeline (free users type manually).

### Layer 1: OCR Extraction

This is the existing OCR system already built in ServLine.

**Input:** Menu image (JPEG, PNG, PDF)

**Process:**
1. Pre-process the image (deskew, enhance contrast, handle rotation)
2. Run OCR to extract all visible text
3. Parse extracted text into structured data:
   - Identify category headers vs item names vs descriptions vs prices
   - Group items under correct categories
   - Extract modifiers and sub-options where present
   - Handle multi-column layouts
   - Handle multi-page menus

**Output:** A first-pass structured menu object. This output WILL contain errors — misspelled words, wrong prices, missed items, misassigned categories. That is expected and acceptable. Layer 2 exists to fix these errors.

### Layer 2: Claude API Verification and Correction

**THIS IS A NEW COMPONENT TO BE BUILT.**

This is the critical differentiator. After Layer 1 produces its best-effort parse, Layer 2 sends BOTH the original menu image AND the Layer 1 output to the Claude API. Claude independently reads the image and verifies every single piece of data, correcting errors and filling gaps through process of elimination.

**Input:**
- The original menu image (the same image Layer 1 processed)
- The structured data output from Layer 1

**What Claude is instructed to do:**

1. **Read the menu image independently.** Claude can see images. It reads the entire menu directly from the image without relying on the OCR output.

2. **Compare every line item against the OCR output.**
   - Is each item name spelled correctly? If not, fix it based on what the image shows.
   - Is each price correct? If not, fix it based on what the image shows.
   - Is each description accurate and complete? If not, fix it.
   - Is each item in the correct category? If not, move it.

3. **Find missing items through process of elimination.**
   - Count the number of items visible in each category on the menu image
   - Compare against the number of items the OCR captured in each category
   - If there is a discrepancy, identify the specific missing item by reading the image and comparing against the OCR list
   - Add the missing item with correct name, description, price, and category
   - Do NOT guess or invent items. Only add items clearly visible on the menu image.

4. **Remove duplicates.** If OCR captured the same item twice, identify and remove the duplicate.

5. **Verify category structure.** Confirm all category headers from the image are represented. Confirm no item names were misidentified as categories or vice versa.

6. **Return the corrected data** with a log of every change made.

**Claude API Call Implementation:**

```python
import anthropic
import json

client = anthropic.Anthropic()

def verify_and_correct_menu(menu_image_base64, ocr_output, image_media_type="image/jpeg"):
    """
    Layer 2: Send the original menu image and OCR output to Claude
    for verification and correction.
    
    Args:
        menu_image_base64: Base64-encoded menu image
        ocr_output: Structured dict from Layer 1 OCR
        image_media_type: MIME type of the image
    
    Returns:
        dict with corrected menu data, changes log, and confidence score
    """
    
    response = client.messages.create(
        model="claude-sonnet-4-5-20250514",
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_media_type,
                            "data": menu_image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": """You are a menu verification system. You have two inputs:

1. The menu image above — this is the SOURCE OF TRUTH.
2. The OCR-extracted data below — this is an ATTEMPT that contains errors.

Your job is to produce a perfectly corrected version of the menu data by comparing the OCR output against what you can actually see in the image.

## Instructions

### Step 1: Read the menu image independently
Read the entire menu from the image yourself. Note every category, every item name, every description, every price, and every modifier you can see. Do not rely on the OCR output for this step.

### Step 2: Compare and correct every line item
For every item in the OCR output, compare against what you read from the image:
- Fix any misspelled or garbled item names to match what the image shows
- Fix any incorrect prices to match what the image shows
- Fix any inaccurate or incomplete descriptions
- Fix any items placed in the wrong category

### Step 3: Find missing items using process of elimination
- Count the items you can see in each category on the menu image
- Compare against what the OCR captured in each category
- If any items are missing from the OCR output, add them with the correct name, description, price, and category as you read them from the image
- ONLY add items you can clearly see on the menu image. Do NOT guess or invent items.

### Step 4: Remove duplicates
- If the same item appears more than once in the OCR output, keep only one instance

### Step 5: Verify category structure
- Confirm all category headers from the image are represented in the data
- Confirm no item names were misidentified as categories
- Confirm no category headers were misidentified as items
- Confirm items are grouped under the correct category

## OCR Output to Verify:
"""
                            + json.dumps(ocr_output, indent=2)
                            + """

## CRITICAL: Response Format
Respond with ONLY valid JSON. No preamble, no markdown backticks, no explanation outside the JSON.

{
  "corrected_menu": {
    "categories": [
      {
        "name": "Category Name",
        "items": [
          {
            "name": "Item Name",
            "description": "Item description or empty string if none",
            "price": 0.00,
            "modifiers": [
              {
                "name": "Modifier Group Name",
                "options": ["Option 1", "Option 2"],
                "required": true or false
              }
            ]
          }
        ]
      }
    ]
  },
  "changes_made": [
    {
      "type": "spelling_fix | price_fix | description_fix | missing_item_added | duplicate_removed | category_fix",
      "detail": "Human-readable description of what was changed"
    }
  ],
  "confidence": 0.0,
  "total_items": 0,
  "total_categories": 0,
  "items_corrected": 0,
  "items_added": 0,
  "items_removed": 0
}"""
                    }
                ]
            }
        ]
    )
    
    # Parse Claude's response
    response_text = response.content[0].text
    
    # Strip markdown backticks if present
    clean_text = response_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    if clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    
    try:
        result = json.loads(clean_text)
        return result
    except json.JSONDecodeError:
        # If parsing fails, retry once
        # If retry also fails, fall back to OCR output with lowered confidence
        return {
            "corrected_menu": ocr_output,
            "changes_made": [],
            "confidence": 0.5,
            "total_items": 0,
            "total_categories": 0,
            "items_corrected": 0,
            "items_added": 0,
            "items_removed": 0,
            "verification_failed": True
        }
```

**Implementation Notes:**

- Use `claude-sonnet-4-5-20250514` — fast enough for this task, cheaper than Opus, more than capable for image comparison.
- For multi-page menus, send ALL page images in the same API call so Claude has full context for process of elimination.
- The `changes_made` log is for internal use only. It is never shown to the restaurant owner. Use it to track patterns in OCR errors and improve Layer 1 over time.
- If Claude's response fails to parse as JSON, retry the API call once. If it fails a second time, fall back to the Layer 1 OCR output and set the confidence score low enough to trigger the confidence gate.
- Expected API cost: approximately $0.05-0.50 per menu depending on size and number of pages. Trivial against $50 per-parse revenue or $25-35 POS per-parse fee.

### Confidence Gate

After Layer 2 returns its results, check the confidence score.

**Threshold: 90%**

**If confidence >= 0.90:**
- The data is good. Proceed.
- POS API experience: return verified JSON to the POS system
- Website experience: load verified data into the ServLine editor

**If confidence < 0.90:**
- Do NOT deliver the data.
- POS API experience: return the `low_confidence` response (see API response format above). The POS system displays a message asking for a clearer photo.
- Website experience: display on screen: "We need a clearer photo to get the best results. Please retake your menu photo with better lighting, less glare, and make sure all text is readable."
- Log the failed attempt, confidence score, and identified issues for analysis.

**The confidence score is NEVER shown to the restaurant owner or the POS company.** It is purely an internal quality gate. The user either gets clean data or gets asked for a better photo. There is no in-between. No confidence ratings, no color coding, no "check these items." The system is either confident or it asks for better input.

---

## Full Pipeline Flow

```
MENU IMAGE RECEIVED
(from POS API or website upload)
         |
         v
┌─────────────────────┐
│  IMAGE PRE-PROCESS   │
│  (deskew, enhance,   │
│   rotation, contrast)│
└──────────┬──────────┘
           |
           v
┌─────────────────────┐
│  LAYER 1: OCR        │
│  (text extraction +  │
│   structuring into   │
│   categories, items, │
│   prices, modifiers) │
└──────────┬──────────┘
           |
           v
┌──────────────────────────────────┐
│  LAYER 2: CLAUDE API REVIEW      │
│                                  │
│  Input: original image + OCR     │
│  output sent together            │
│                                  │
│  Claude independently reads the  │
│  image and compares line by line │
│  against OCR output:             │
│                                  │
│  - Fixes misspellings            │
│  - Corrects wrong prices         │
│  - Adds missing items (process   │
│    of elimination)               │
│  - Removes duplicates            │
│  - Fixes category assignments    │
│                                  │
│  Output: corrected menu +        │
│  confidence score + changes log  │
└──────────┬───────────────────────┘
           |
           v
┌─────────────────────────┐
│  CONFIDENCE GATE         │
│  Score >= 0.90?          │
└─────┬─────────────┬─────┘
      |             |
   YES |          NO |
      v             v
┌──────────┐  ┌─────────────────────┐
│ DELIVER  │  │ "Please retake your │
│ DATA     │  │  photo with better  │
└─────┬────┘  │  lighting"          │
      |       └─────────────────────┘
      v
┌────────────────────────────────────┐
│  WHICH EXPERIENCE?                  │
│                                    │
│  POS API:                          │
│    Return JSON to POS system.      │
│    POS editor populates.           │
│    Restaurant confirms in POS      │
│    editor. Done.                   │
│                                    │
│  WEBSITE ($50 paid parse):         │
│    Load into ServLine editor.      │
│    Restaurant reviews, edits,      │
│    saves, exports to POS/CSV/JSON. │
│    Done.                           │
└────────────────────────────────────┘
```

---

## POS Integrations to Build

ServLine must support direct export to the following POS systems at launch, plus universal file export as a fallback.

### Launch Integrations

**1. Square**
- Priority: FIRST — simplest API, best documentation, largest independent restaurant user base
- Auth: OAuth 2.0 — restaurant logs into Square through ServLine, grants permission
- Integration: Use Square Catalog API to create menu items, categories, modifiers
- Needs: Square Developer account and sandbox for testing

**2. Toast**
- Priority: SECOND — dominant restaurant-specific POS, more complex menu hierarchy
- Auth: OAuth via Toast Developer API — requires partner program application (apply early, approval may take time)
- Integration: Use Toast Configuration API to create menus, menu groups, menu items, option groups
- Note: Toast has a nested hierarchy (Menu > MenuGroup > MenuItem > MenuOptionGroup > MenuItem). Data mapping is more complex than Square.
- Needs: Toast Developer Program access and sandbox

**3. Clover**
- Priority: THIRD — strong hardware ecosystem, has app marketplace (potential distribution channel)
- Auth: OAuth 2.0
- Integration: Use Clover REST API to create items, categories, modifier groups
- Needs: Clover Developer account and sandbox

### Universal Fallback

For restaurants on any POS system not directly supported:
- **CSV export** — formatted as a clean spreadsheet with columns for category, item name, description, price, modifiers
- **JSON export** — the same structured data ServLine uses internally

### Integration Architecture

Each POS integration needs:
1. **OAuth flow** — login page, token storage, token refresh, error handling for revoked permissions
2. **Data mapping layer** — translates ServLine's universal menu JSON into the specific POS system's data schema
3. **API calls** — creates categories, items, modifiers, and option groups in the correct order (most POS systems require categories to exist before items can be assigned to them)
4. **Error handling** — graceful failure if API calls fail mid-upload, rate limiting handling, character limit validation, duplicate detection
5. **Confirmation** — verify the menu was fully created in the POS system and report success or any issues back to the user

### Critical Rule

The core pipeline (`core/`) must have ZERO knowledge of which POS system the data is going to. It processes an image and returns clean structured data. The POS integration layer sits on top and handles the translation and delivery. This separation ensures improvements to the core engine benefit all integrations automatically.

---

## File and Module Structure

```
servline/
├── core/                              # Shared engine — both experiences use this
│   ├── ocr/                           # Layer 1: OCR extraction
│   │   ├── preprocessor.py            # Image preprocessing (deskew, enhance, etc.)
│   │   ├── extractor.py               # OCR text extraction
│   │   └── parser.py                  # Structure raw text into menu data
│   ├── verification/                  # Layer 2: Claude API verification (NEW)
│   │   ├── claude_reviewer.py         # Claude API call for verification/correction
│   │   └── confidence.py              # Confidence scoring and gate logic
│   └── pipeline.py                    # Orchestrates Layer 1 -> Layer 2 -> Confidence Gate
│
├── api/                               # Experience 1: POS company API
│   ├── endpoints.py                   # API routes (receive image, return JSON)
│   ├── auth.py                        # POS partner authentication
│   └── schemas.py                     # Request/response JSON schemas
│
├── web/                               # Experience 2: Direct website
│   ├── routes.py                      # Website page routes
│   ├── editor/                        # The ServLine review/manual editor
│   │   ├── views.py                   # Editor page views
│   │   └── templates/                 # Editor HTML templates
│   ├── accounts/                      # User accounts
│   │   ├── auth.py                    # Login/signup
│   │   └── models.py                  # User and saved menu models
│   └── export/                        # Export functionality
│       └── exporters.py               # CSV, JSON export logic
│
├── integrations/                      # POS system integrations (both experiences use these)
│   ├── square/
│   │   ├── oauth.py                   # Square OAuth flow
│   │   ├── mapper.py                  # Map ServLine data to Square schema
│   │   └── client.py                  # Square API calls to create menu items
│   ├── toast/
│   │   ├── oauth.py                   # Toast OAuth flow
│   │   ├── mapper.py                  # Map ServLine data to Toast schema
│   │   └── client.py                  # Toast API calls to create menu items
│   ├── clover/
│   │   ├── oauth.py                   # Clover OAuth flow
│   │   ├── mapper.py                  # Map ServLine data to Clover schema
│   │   └── client.py                  # Clover API calls to create menu items
│   └── base.py                        # Shared integration interface/base class
│
├── models/                            # Shared data models
│   ├── menu.py                        # Menu, Category, Item, Modifier models
│   └── parse_log.py                   # Logging for parses, changes, confidence scores
│
└── config/
    ├── settings.py                    # API keys, thresholds, environment config
    └── pos_partners.py                # POS partner configs for API experience
```

**Key architecture rules:**
- `core/` has ZERO knowledge of POS systems, the website, or the API. It takes an image in and returns structured data out.
- `integrations/` is shared by BOTH experiences. A POS company API customer and a direct website customer both use the same Square integration code.
- `api/` handles the POS company API experience only.
- `web/` handles the direct website experience only.
- Any improvement to `core/` automatically benefits both experiences.
- Any new POS integration added to `integrations/` is automatically available to both experiences.

---

## Implementation Priority

1. **Build `core/verification/claude_reviewer.py`** — the Layer 2 Claude API verification call. This is the most important new component. The existing OCR is Layer 1. This wraps around it and corrects its output.

2. **Build `core/verification/confidence.py`** — the confidence gate logic with the 90% threshold.

3. **Build `core/pipeline.py`** — the orchestrator that chains Layer 1 (existing OCR) → Layer 2 (Claude verification) → Confidence Gate and returns the final result.

4. **Build `integrations/square/`** — the first POS integration. OAuth flow, data mapping, API calls to create menu items in Square.

5. **Build `integrations/toast/`** — second POS integration. Apply for Toast Developer Program access early since approval may take time.

6. **Build `integrations/clover/`** — third POS integration.

7. **Build `api/`** — the POS company API endpoints. This is the interface that POS partners call. It uses `core/pipeline.py` for processing and `integrations/` for delivery.

8. **Update `web/`** — ensure the existing website and editor call `core/pipeline.py` instead of calling OCR directly, so website users get the benefit of Layer 2 verification. Add the $50 payment gate. Add POS export options (Square, Toast, Clover) alongside existing CSV/JSON export. Ensure the free tier uses the editor WITHOUT triggering the AI pipeline.

**The existing OCR system and editor do not need to be rewritten.** Layer 2 is additive — it wraps around the existing OCR output. The editor continues to function as-is for manual entry (free tier) and for reviewing AI-parsed results (paid tier).

---

## Summary: Two Products, One Engine

| | POS Company API | Direct Website |
|---|---|---|
| Who uses it | Restaurant owner during POS onboarding | Restaurant owner on servline.com |
| How they access it | Built into POS setup flow | Directly at servline.com |
| What they see | Their POS editor, pre-populated | ServLine's editor |
| ServLine branding | No — invisible, white-label | Yes — full ServLine experience |
| Editor used | POS company's own editor | ServLine's editor |
| Free tier | N/A — POS company pays | Manual editor with save and export |
| Paid tier | POS company pays base + per-parse | $50 one-time for AI-powered parse |
| AI pipeline used | Always (every parse) | Only for $50 paid parses |
| Export method | API returns JSON to POS system | User exports to CSV, JSON, or direct POS |
| Menu saved | No — stateless API | Yes — saved to user account |
| Revenue source | Monthly base + per-parse from POS company | $50 one-time from restaurant |
