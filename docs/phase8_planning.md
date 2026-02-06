# Phase 8 Planning: Semantic Menu Intelligence

**Date**: Day 50 (February 6, 2026)
**Status**: Planning Complete
**Phase 7 Status**: Complete (OCR hardening validated)

---

## Executive Summary

Phase 8 focuses on improving semantic understanding of menu content. With OCR extraction now stable (Phase 7 complete), we shift focus to:

1. **Deep dish/ingredient grammar** - Better understanding of menu item structure
2. **Portion & variant logic** - Improved size/variant detection and normalization
3. **Cross-item consistency** - Validate pricing, categories across items
4. **Higher-confidence category semantics** - Better category inference

---

## Current State Analysis

### 1. Category Inference (`category_infer.py` - 347 LOC)

**Current Capabilities:**
- Keyword-based matching per category
- Price band validation (rough ranges)
- Neighbor context scoring
- Confidence scores (0-100)

**Limitations:**
- Single keyword matching, no phrase understanding
- No grammatical context ("Buffalo Chicken" vs "Chicken Buffalo Wings")
- Price bands are static, don't adapt to restaurant pricing
- No heading/section awareness from OCR layout

**Phase 8 Improvements:**
| Priority | Improvement | Complexity |
|----------|-------------|------------|
| HIGH | Add phrase-level keyword matching | Low |
| HIGH | Use geometric headings from OCR for category assignment | Medium |
| MEDIUM | Adaptive price bands based on detected menu prices | Medium |
| LOW | Multi-word pattern matching ("BBQ Chicken Pizza") | Low |

---

### 2. Variant Engine (`variant_engine.py` - 331 LOC)

**Current Capabilities:**
- Size normalization (inches, pieces, S/M/L)
- Flavor detection (hot, mild, bbq, etc.)
- Style detection (bone-in, thin crust, etc.)
- Group key generation for clustering

**Limitations:**
- No portion inference ("half", "whole", "party size")
- No combo/meal detection ("with drink", "includes fries")
- No cross-variant price validation
- Limited pizza crust vocabulary

**Phase 8 Improvements:**
| Priority | Improvement | Complexity |
|----------|-------------|------------|
| HIGH | Add portion keywords (half, whole, family, party) | Low |
| HIGH | Expand pizza crust types (pan, hand-tossed, brooklyn) | Low |
| MEDIUM | Meal/combo detection for bundled items | Medium |
| MEDIUM | Validate variant prices are ordered (S < M < L) | Medium |
| LOW | Support fractional sizes ("1/2 sheet", "quarter pie") | Low |

---

### 3. Price Integrity (`price_integrity.py` - 414 LOC)

**Current Capabilities:**
- Decimal shift correction (1600 → $16.00)
- Outlier detection via IQR
- Side/coupon price classification
- Group median/IQR statistics

**Limitations:**
- No cross-category price validation
- No multi-size price progression checks
- Side detection is purely textual
- Coupon lines sometimes include real prices

**Phase 8 Improvements:**
| Priority | Improvement | Complexity |
|----------|-------------|------------|
| HIGH | Validate size-based pricing progression | Medium |
| HIGH | Flag suspiciously high/low category prices | Low |
| MEDIUM | Cross-category price sanity (salad < pizza entree) | Medium |
| MEDIUM | Better coupon parsing to extract component prices | High |
| LOW | Detect likely typos in prices (99.99 vs 9.99) | Medium |

---

### 4. Category Hierarchy (`category_hierarchy.py` - 493 LOC)

**Current Capabilities:**
- Category alias collapsing ("NY Style Pizza" → "Pizza")
- Subcategory inference per item
- Slug generation for paths
- Grouped structure for exports

**Limitations:**
- No geometric heading detection (font size, position)
- Subcategory inference is keyword-only
- No section break detection from OCR layout
- No confidence scoring for subcategories

**Phase 8 Improvements:**
| Priority | Improvement | Complexity |
|----------|-------------|------------|
| HIGH | Implement geometric heading detection | High |
| HIGH | Use OCR block metadata for section breaks | Medium |
| MEDIUM | Add subcategory confidence scoring | Low |
| MEDIUM | Detect menu "sections" by vertical gaps | Medium |
| LOW | Support nested subcategories (Pizza > Specialty > Meat Lovers) | High |

---

### 5. AI Cleanup (`ai_cleanup.py` - 751 LOC)

**Current Capabilities:**
- Text normalization (whitespace, punctuation)
- Long-name rescue (split to name + description)
- Ingredient smoothing and phrase preservation
- Smart title casing

**Limitations:**
- No grammar-based item structure parsing
- No ingredient tokenization for structured export
- Description cleanup sometimes loses detail
- No understanding of menu item components

**Phase 8 Improvements:**
| Priority | Improvement | Complexity |
|----------|-------------|------------|
| HIGH | Grammar-based item parser (name, desc, modifiers) | High |
| HIGH | Better long-name split heuristics | Medium |
| MEDIUM | Structured ingredient list extraction | High |
| MEDIUM | Preserve important qualifiers ("gluten-free", "vegetarian") | Low |
| LOW | Detect and normalize allergen information | Medium |

---

## Phase 8 Work Items (Prioritized)

### Sprint 8.1 - Core Grammar & Structure (Days 51-55)

1. **Deep Dish/Ingredient Grammar**
   - [ ] Create menu item grammar parser
   - [ ] Identify item components: base, toppings, modifiers, sizes
   - [ ] Handle compound items ("Meat Lovers with extra cheese")

2. **Enhanced Long-Name Parsing**
   - [ ] Improve split heuristics in `ai_cleanup.py`
   - [ ] Detect natural break points (size, price, description start)
   - [ ] Preserve item identity while extracting details

### Sprint 8.2 - Variant & Portion Logic (Days 56-60)

3. **Portion Detection Enhancement**
   - [ ] Add portion keywords to `variant_engine.py`
   - [ ] Handle pizza portion terms (slice, whole, half)
   - [ ] Support wing count variations (6pc, 12pc, 24pc)

4. **Variant Price Validation**
   - [ ] Implement size → price ordering checks
   - [ ] Flag inverted price progressions
   - [ ] Cross-reference variant families

### Sprint 8.3 - Cross-Item Consistency (Days 61-65)

5. **Price Consistency Checks**
   - [ ] Validate similar items have similar prices
   - [ ] Detect category outliers (cheap pizza, expensive soda)
   - [ ] Flag suspicious duplicates with different prices

6. **Category Consistency**
   - [ ] Ensure similar items share categories
   - [ ] Detect miscategorized items by price/name patterns
   - [ ] Improve neighbor-based category smoothing

### Sprint 8.4 - Semantic Confidence (Days 66-70)

7. **Geometric Heading Detection**
   - [ ] Use OCR block heights for heading detection
   - [ ] Implement section break detection via gaps
   - [ ] Promote large-font blocks to category headings

8. **Higher-Confidence Categories**
   - [ ] Add phrase-level keyword matching
   - [ ] Implement multi-signal category scoring
   - [ ] Add confidence tiers (high/medium/low/unknown)

---

## Success Metrics

| Metric | Current | Phase 8 Target |
|--------|---------|----------------|
| Category accuracy | ~75% | 90%+ |
| Variant detection rate | ~60% | 85%+ |
| Price validation coverage | ~40% | 80%+ |
| Heading detection | 0% | 70%+ |
| Item parse success | ~80% | 95%+ |

---

## Technical Dependencies

- Phase 7 OCR output (stable, validated)
- OCR block metadata (`bbox`, `height`, `confidence`)
- Existing semantic modules (no breaking changes)

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Grammar parser complexity | High | Start with pizza-specific grammar, expand |
| False positive category changes | Medium | Keep existing as fallback, require high confidence |
| Geometric detection failures | Medium | Fall back to keyword-based when uncertain |
| Performance degradation | Low | Profile and optimize hot paths |

---

## Next Steps

1. **Day 51**: Begin Sprint 8.1 - Create menu item grammar parser skeleton
2. **Day 52**: Implement pizza-specific grammar rules
3. **Day 53**: Test grammar on validated menus from Day 50
4. **Day 54-55**: Iterate on grammar based on edge cases

Ready to proceed with Phase 8 implementation.
