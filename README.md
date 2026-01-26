# ServLine

ServLine is a **portal + API + AI “One Brain” system** for restaurant menu onboarding:

**OCR + structured imports → living editable menu → export to POS**

The core mission is to eliminate manual menu onboarding by reliably parsing **real-world menus** (photos, PDFs, CSV/XLSX/JSON) into structured, editable data.

This repository follows a **phased, milestone-driven build plan**, with Git commits marking verified progress.

---

## 🎯 Project North Star

> Upload a real restaurant menu → accurately parsed → editable draft → POS-ready export  
> **No manual re-entry. No desktop dependency. No OCR surprises.**

Primary value:
- **Accuracy on messy, real menus**
- **Convenience during onboarding**

Target buyer:
- POS companies (Square, Toast, etc.)

---

## 📁 Repository Structure

```
servline/
portal/        # Flask portal (uploads, drafts, editor, imports)
infra/         # Local infra helpers (ngrok, run/stop scripts)
storage/       # 🧠 One Brain (OCR + semantics + validation)
fixtures/      # Sample menus and test assets
uploads/       # User-uploaded menu files
README.md
```

---

## 🧠 One Brain Architecture (Authoritative)

ServLine uses a **single canonical OCR + AI + semantic brain**:

- No duplicated OCR logic
- No legacy fallbacks
- No parallel pipelines

**Entrypoint:**  
`storage/ocr_facade.py`

Result:
- Predictable behavior
- Auditable OCR decisions
- Debuggable artifacts
- Safe AI cleanup

---

## ✅ Completed Milestones (Verified)

### 🚀 Day 1–14 — Portal, Data Model, Draft Editor
- Core Flask UI
- Database schema
- Draft lifecycle
- Exports
- Error handling

---

### 🚀 Day 15 — Failed Split Attempt (Reverted)
- Experimental change reverted
- Baseline preserved

---

### 🚀 Day 16–19 — OCR Infrastructure & Precision
- OCR pipeline stabilization
- CLAHE, grayscale, sharpening
- Draft Editor refinements

---

### 🚀 Day 20–22 — AI Cleanup (Phase A)
- AI Preview / AI Finalize
- Safe cleanup baseline
- Unified export path

---

### 🚀 Day 23–25 — Phase 3: Semantic Reconstruction
- Rotation preview
- Category inference
- Two-column merge
- Variant detection
- Confidence overlays
- Garbage tuning

**Phase 3 complete.**

---

### 🚀 Day 26–31 — Phase 4: Structural OCR System
- Semantic block understanding
- Multi-line merging
- Variant normalization
- Category hierarchy v2
- Price Integrity Engine v2
- Structured Draft Output v2
- Superimport bundle
- Stability hardening

**Phase 4 complete.**

---

### 🚀 Day 32–35 — Phase 5: AI Text Surgeon
- Non-hallucinating cleanup
- Ingredient smoothing
- Size/variant-aware rewrites
- Price/category/variant protection
- Safety tagging (`[AI Cleaned]`)

**Phase 5 complete.**

---

### 🛠️ Day 36 — Phase 5 Cleanup Day
- Full end-to-end validation
- Integrity guarantees proven
- Quality guards validated

**Day 36 complete.**

---

## 🧠 ONE BRAIN MIGRATION — COMPLETE

All OCR, AI, and semantic logic centralized into `/storage`.

Achievements:
- Single canonical OCR library
- Health endpoint verified
- Legacy OCR retired
- Draft + AI Finalize fully unified

**Result:** ServLine now operates with a true One Brain architecture.

---

## 🧮 Phase 6 — Structured Imports (No OCR)

### Day 37–41 — CSV / XLSX / JSON Imports
- Structured import APIs
- CSV/XLSX/JSON parsing & validation
- Draft Editor compatibility
- Column Mapping UI (CSV/XLSX)
- AI Finalize support
- Unified progress & export flow

**Phase 6 complete.**

---

## 🧠 Phase 7 — Vision & OCR Hardening (COMPLETED)

Phase 7 focused on eliminating OCR unpredictability and hardening the system so results on real-world menus are **deterministic, debuggable, and trustworthy**.

---

### 🧠 Day 42–43 — OCR Path Audit & Debug Stabilization
- Verified single OCR → Draft path
- Removed duplicate routes
- Hardened debug endpoints

---

### 🔧 Day 44 — Maintenance & Diagnosis
- Confirmed OCR input correctness
- Verified debug artifacts
- Identified orientation + scoring issues

---

### 🧠 Day 45 — Orientation Enforcement & OCR Reality Fixes
- Deterministic orientation normalization
- Legacy auto-rotate disabled
- OCR input artifacts persisted
- Numeric corruption fixes

---

### 🧠 Day 46 — Rotation Sweep (Worker Wiring)
- Rotation sweep across 0° / 90° / 180° / 270°
- Quality-based rotation selection
- Debug logging + artifacts
- Verified on rotated PDFs

---

### 🟢 Day 47 — Phase 7 pt.9: Multi-pass OCR Improvements
- Lowered fusion threshold (92 → 70)
- Disabled forced 2-column split
- Disabled incorrect multipass rotation restriction
- OCR reliably recovers text from rotated PDFs

---

### ✅ Day 48 — Phase 7 pt.10: Scoring & Selection (COMPLETE)

- Deterministic winner selection across OCR passes
- Confidence-weighted fusion finalized
- Persisted per-job OCR metadata:
  - `rotation_selected`
  - `psm_selected`
  - `quality_score`
  - rejection flags (non-destructive)
- OCR output now reflects **true recognition quality**, not orientation or scoring artifacts

**Phase 7 complete.**

---

## ▶️ CURRENT POSITION

➡ **Phase 8 — Semantic Menu Intelligence (UNLOCKED)**

With OCR extraction now stable and trustworthy, the system is ready to advance into higher-order semantic reasoning.

---

## 🌄 System State Summary

ServLine now has:

- ✅ Unified OCR brain
- ✅ Stable import flow (PDF/Image/CSV/XLSX/JSON)
- ✅ Deterministic orientation handling
- ✅ Rotation sweep for mis-rotated uploads
- ✅ Deterministic OCR scoring & selection
- ✅ Full debug artifacts and metadata
- ✅ Price-safe, category-safe AI cleanup
- ✅ Structured Draft Editor
- ✅ Column mapping for structured imports

---

## ⏭️ Next Execution Phase

**Phase 8 — Semantic Menu Intelligence**
- Deep dish/ingredient grammar
- Portion & variant logic
- Cross-item consistency
- Higher-confidence category semantics

With Phase 7 complete, all downstream work can proceed without OCR instability masking real issues.
