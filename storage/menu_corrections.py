"""
Menu OCR Corrections Module for ServLine
=========================================
Supplementary module for post-OCR text cleanup.

Two-layer approach:
1. Direct dictionary lookup (200+ menu-specific OCR errors)
2. Fuzzy matching against food vocabulary (for unknown errors)

Usage:
    from menu_corrections import correct_ocr_text, correct_menu_item
    
    # Single correction
    fixed = correct_ocr_text("chlcken")  # Returns "chicken"
    
    # Full menu item cleanup
    item = correct_menu_item("Grllled Chlcken Sandwlch")  # Returns "Grilled Chicken Sandwich"

Based on research from:
- Obad94/OCR-Menu-Reader (200+ corrections)
- Common OCR character confusions (l/1/I, O/0, rn/m, etc.)
- Food/restaurant vocabulary lists
"""

from difflib import SequenceMatcher
from typing import Optional, Dict, List, Tuple
import re

# =============================================================================
# LAYER 1: Direct OCR Error → Correction Dictionary
# =============================================================================
# Sources: Obad94/OCR-Menu-Reader + custom additions for common OCR confusions

OCR_CORRECTIONS: Dict[str, str] = {
    # --- PROTEINS ---
    'chlcken': 'chicken',
    'chiken': 'chicken',
    'chickan': 'chicken',
    'cnicken': 'chicken',
    'ch1cken': 'chicken',
    'chleken': 'chicken',
    'chickен': 'chicken',
    'chіcken': 'chicken',  # Cyrillic і
    
    'веef': 'beef',
    'bееf': 'beef',  # Cyrillic е
    'beеf': 'beef',
    'beet': 'beef',  # Common OCR error
    
    'роrk': 'pork',
    'pоrk': 'pork',  # Cyrillic о
    'рork': 'pork',  # Cyrillic р
    
    'flsh': 'fish',
    'f1sh': 'fish',
    'fіsh': 'fish',
    
    'shrіmp': 'shrimp',
    'shrlmp': 'shrimp',
    'shr1mp': 'shrimp',
    'shrinp': 'shrimp',
    'shnmp': 'shrimp',
    
    'lobstar': 'lobster',
    'lobstеr': 'lobster',
    'l0bster': 'lobster',
    
    'turкey': 'turkey',
    'turkеy': 'turkey',
    
    'salnon': 'salmon',
    'salrnon': 'salmon',
    'sa1mon': 'salmon',
    'salm0n': 'salmon',
    
    'tоfu': 'tofu',
    't0fu': 'tofu',
    
    'laмb': 'lamb',
    'larnb': 'lamb',
    '1amb': 'lamb',
    
    # --- SEAFOOD ---
    'sesfoco': 'seafood',
    'seatood': 'seafood',
    'seafocd': 'seafood',
    'seaf0od': 'seafood',
    'seafооd': 'seafood',
    
    'crаb': 'crab',
    'сrab': 'crab',
    
    'оyster': 'oyster',
    '0yster': 'oyster',
    'oystar': 'oyster',
    
    'scаllop': 'scallop',
    'scall0p': 'scallop',
    'scaliop': 'scallop',
    
    'clarn': 'clam',
    'clam': 'clam',
    'c1am': 'clam',
    
    'calamаri': 'calamari',
    'calamar1': 'calamari',
    'ca1amari': 'calamari',
    
    # --- APPETIZERS / CATEGORIES ---
    'appetlzers': 'appetizers',
    'apetizers': 'appetizers',
    'appetіzers': 'appetizers',
    'appet1zers': 'appetizers',
    'appetizеrs': 'appetizers',
    'аppetizers': 'appetizers',
    
    'stаrters': 'starters',
    'startеrs': 'starters',
    
    'entreеs': 'entrees',
    'entréеs': 'entrees',
    'entrées': 'entrees',
    'еntrees': 'entrees',
    
    'мains': 'mains',
    'ma1ns': 'mains',
    'maіns': 'mains',
    
    'sіdes': 'sides',
    's1des': 'sides',
    'sidеs': 'sides',
    
    'dessеrts': 'desserts',
    'dеsserts': 'desserts',
    'dessеrt': 'dessert',
    'deserts': 'desserts',  # Common spelling error
    
    'beverаges': 'beverages',
    'bеverages': 'beverages',
    'bevеrages': 'beverages',
    
    'speсials': 'specials',
    'spеcials': 'specials',
    'spec1als': 'specials',
    'speclals': 'specials',
    
    # --- COOKING METHODS ---
    'grllled': 'grilled',
    'gri1led': 'grilled',
    'grіlled': 'grilled',
    'gnlled': 'grilled',
    'grilicd': 'grilled',
    
    'frіed': 'fried',
    'fr1ed': 'fried',
    'fned': 'fried',
    
    'bakеd': 'baked',
    'bаked': 'baked',
    
    'roаsted': 'roasted',
    'rоasted': 'roasted',
    'r0asted': 'roasted',
    
    'steamеd': 'steamed',
    'stеamed': 'steamed',
    'stearned': 'steamed',
    
    'sautéеd': 'sauteed',
    'sаuteed': 'sauteed',
    'sautеed': 'sauteed',
    
    'brаised': 'braised',
    'bra1sed': 'braised',
    
    'smoкed': 'smoked',
    'smokеd': 'smoked',
    'srnoked': 'smoked',
    
    'crіspy': 'crispy',
    'cr1spy': 'crispy',
    
    # --- DIETARY TERMS ---
    'vegetarlan': 'vegetarian',
    'vegeterlan': 'vegetarian',
    'vegetar1an': 'vegetarian',
    'vegatarian': 'vegetarian',
    
    'vegаn': 'vegan',
    'vеgan': 'vegan',
    
    'glutеn': 'gluten',
    'g1uten': 'gluten',
    
    'оrganic': 'organic',
    '0rganic': 'organic',
    'organlc': 'organic',
    
    # --- COMMON FOODS ---
    'salаd': 'salad',
    'sa1ad': 'salad',
    'satad': 'salad',
    'salac': 'salad',
    
    'sоup': 'soup',
    's0up': 'soup',
    'souр': 'soup',
    
    'sandwlch': 'sandwich',
    'sandw1ch': 'sandwich',
    'sandwіch': 'sandwich',
    'sandwhich': 'sandwich',
    'sanwich': 'sandwich',
    
    'burgеr': 'burger',
    'bиrger': 'burger',
    'burger': 'burger',
    
    'pіzza': 'pizza',
    'p1zza': 'pizza',
    'plzza': 'pizza',
    'рizza': 'pizza',
    
    'pаsta': 'pasta',
    'рasta': 'pasta',
    
    'rіce': 'rice',
    'r1ce': 'rice',
    'rlce': 'rice',
    
    'breаd': 'bread',
    'brеad': 'bread',
    
    'chееse': 'cheese',
    'сheese': 'cheese',
    'cheеse': 'cheese',
    
    'tаco': 'taco',
    'tac0': 'taco',
    
    'burrlto': 'burrito',
    'burrit0': 'burrito',
    'burrіto': 'burrito',
    
    'quesadіlla': 'quesadilla',
    'quesad1lla': 'quesadilla',
    
    'naсhos': 'nachos',
    'nach0s': 'nachos',
    
    'wіngs': 'wings',
    'w1ngs': 'wings',
    
    'frіes': 'fries',
    'fr1es': 'fries',
    
    'onіon': 'onion',
    '0nion': 'onion',
    'оnion': 'onion',
    
    # --- SPICES & FLAVORS ---
    'spіcy': 'spicy',
    'sp1cy': 'spicy',
    
    'swеet': 'sweet',
    'swееt': 'sweet',
    
    'sоur': 'sour',
    's0ur': 'sour',
    
    'sаlty': 'salty',
    'sa1ty': 'salty',
    
    'gаrlic': 'garlic',
    'garl1c': 'garlic',
    'garllc': 'garlic',
    
    'hеrb': 'herb',
    'hеrbs': 'herbs',
    
    'sаuce': 'sauce',
    'saucе': 'sauce',
    
    'мarinade': 'marinade',
    'mar1nade': 'marinade',
    
    # --- MEAL TIMES ---
    'breaklast': 'breakfast',
    'breaktast': 'breakfast',
    'brеakfast': 'breakfast',
    
    'lunсh': 'lunch',
    '1unch': 'lunch',
    
    'dlnner': 'dinner',
    'd1nner': 'dinner',
    'dіnner': 'dinner',
    
    'brunсh': 'brunch',
    
    # --- DRINKS ---
    'сoffee': 'coffee',
    'coffее': 'coffee',
    'c0ffee': 'coffee',
    
    'tеa': 'tea',
    'tеа': 'tea',
    
    'juіce': 'juice',
    'ju1ce': 'juice',
    
    'smoothіe': 'smoothie',
    'smooth1e': 'smoothie',
    
    'сocktail': 'cocktail',
    'cockta1l': 'cocktail',
    
    'wіne': 'wine',
    'w1ne': 'wine',
    
    'bееr': 'beer',
    'bеer': 'beer',
    
    # --- SIZES ---
    'sмall': 'small',
    'sma1l': 'small',
    'srnall': 'small',
    
    'мedium': 'medium',
    'med1um': 'medium',
    'mediurn': 'medium',
    
    'largе': 'large',
    '1arge': 'large',
    
    # --- PIZZA SPECIFIC ---
    'pepperonі': 'pepperoni',
    'pepperon1': 'pepperoni',
    'pepperоni': 'pepperoni',
    
    'мushroom': 'mushroom',
    'mushr0om': 'mushroom',
    'mushroon': 'mushroom',
    'mushrooм': 'mushroom',
    
    'sаusage': 'sausage',
    'sausagе': 'sausage',
    
    'anchovіes': 'anchovies',
    'anchov1es': 'anchovies',
    
    'olіves': 'olives',
    '0lives': 'olives',
    'ol1ves': 'olives',
    
    'јalapeno': 'jalapeno',
    'jalapen0': 'jalapeno',
    'jalapenо': 'jalapeno',
    
    'mozzarеlla': 'mozzarella',
    'mozzare1la': 'mozzarella',
    'mozzarella': 'mozzarella',
    
    'parmеsan': 'parmesan',
    'parmesan': 'parmesan',
    
    # --- COMMON OCR GARBAGE PATTERNS ---
    # These catch systematic character confusions
}

# =============================================================================
# LAYER 2: Food Vocabulary for Fuzzy Matching
# =============================================================================
# Used when dictionary lookup fails - find closest valid food word

FOOD_VOCABULARY: set = {
    # Proteins
    'chicken', 'beef', 'pork', 'fish', 'shrimp', 'lobster', 'crab', 'salmon',
    'tuna', 'cod', 'tilapia', 'turkey', 'duck', 'lamb', 'veal', 'bacon',
    'ham', 'sausage', 'steak', 'ribs', 'brisket', 'tenderloin', 'tofu',
    'tempeh', 'seitan',
    
    # Vegetables
    'tomato', 'lettuce', 'onion', 'pepper', 'mushroom', 'spinach', 'kale',
    'broccoli', 'cauliflower', 'carrot', 'celery', 'cucumber', 'zucchini',
    'squash', 'eggplant', 'asparagus', 'artichoke', 'avocado', 'corn',
    'potato', 'sweet potato', 'beans', 'peas', 'cabbage', 'sprouts',
    
    # Fruits
    'apple', 'banana', 'orange', 'lemon', 'lime', 'strawberry', 'blueberry',
    'raspberry', 'mango', 'pineapple', 'peach', 'pear', 'grape', 'cherry',
    'watermelon', 'cantaloupe', 'coconut', 'fig', 'date', 'pomegranate',
    
    # Grains & Carbs
    'rice', 'pasta', 'bread', 'noodles', 'quinoa', 'couscous', 'barley',
    'oats', 'tortilla', 'pita', 'naan', 'baguette', 'ciabatta', 'focaccia',
    
    # Dairy & Cheese
    'cheese', 'mozzarella', 'parmesan', 'cheddar', 'swiss', 'provolone',
    'feta', 'gouda', 'brie', 'goat cheese', 'cream cheese', 'ricotta',
    'butter', 'cream', 'milk', 'yogurt',
    
    # Dishes
    'pizza', 'burger', 'sandwich', 'salad', 'soup', 'stew', 'curry',
    'pasta', 'risotto', 'lasagna', 'ravioli', 'gnocchi', 'taco', 'burrito',
    'quesadilla', 'enchilada', 'nachos', 'fajita', 'sushi', 'ramen',
    'pho', 'pad thai', 'stir fry', 'fried rice', 'wings', 'nuggets',
    'fingers', 'strips', 'wrap', 'roll', 'bowl', 'platter',
    
    # Categories
    'appetizer', 'appetizers', 'starter', 'starters', 'entree', 'entrees',
    'main', 'mains', 'side', 'sides', 'dessert', 'desserts', 'beverage',
    'beverages', 'drink', 'drinks', 'special', 'specials', 'combo', 'combos',
    
    # Cooking Methods
    'grilled', 'fried', 'baked', 'roasted', 'steamed', 'sauteed', 'braised',
    'smoked', 'blackened', 'poached', 'broiled', 'pan-seared', 'crispy',
    'stuffed', 'glazed', 'marinated',
    
    # Descriptors
    'fresh', 'homemade', 'house', 'signature', 'classic', 'traditional',
    'spicy', 'mild', 'hot', 'sweet', 'savory', 'tangy', 'zesty', 'creamy',
    'crunchy', 'tender', 'juicy', 'seasoned', 'herb', 'garlic', 'lemon',
    
    # Sizes
    'small', 'medium', 'large', 'extra large', 'personal', 'regular',
    'half', 'full', 'single', 'double', 'triple',
    
    # Pizza Toppings
    'pepperoni', 'sausage', 'mushroom', 'mushrooms', 'olive', 'olives',
    'onion', 'onions', 'pepper', 'peppers', 'jalapeno', 'jalapenos',
    'anchovy', 'anchovies', 'pineapple', 'ham', 'bacon', 'spinach',
    'tomato', 'tomatoes', 'basil', 'garlic', 'artichoke',
    
    # Sauces
    'sauce', 'marinara', 'alfredo', 'pesto', 'ranch', 'buffalo', 'bbq',
    'barbecue', 'teriyaki', 'honey', 'mustard', 'mayo', 'aioli', 'salsa',
    'guacamole', 'hummus', 'tzatziki', 'gravy', 'hollandaise',
    
    # Drinks
    'coffee', 'tea', 'juice', 'soda', 'water', 'lemonade', 'smoothie',
    'shake', 'milkshake', 'wine', 'beer', 'cocktail', 'margarita',
    
    # Desserts
    'cake', 'pie', 'ice cream', 'gelato', 'sorbet', 'brownie', 'cookie',
    'cheesecake', 'tiramisu', 'mousse', 'pudding', 'cobbler', 'sundae',
}

# =============================================================================
# CHARACTER CONFUSION PATTERNS
# =============================================================================
# OCR commonly confuses visually similar characters

CHAR_CONFUSIONS: Dict[str, List[str]] = {
    'l': ['1', 'I', '|', 'i'],
    '1': ['l', 'I', '|', 'i'],
    'I': ['l', '1', '|', 'i'],
    'O': ['0', 'o', 'Q'],
    '0': ['O', 'o', 'Q'],
    'o': ['0', 'O'],
    'S': ['5', '$'],
    '5': ['S', '$'],
    'B': ['8', '3'],
    '8': ['B', '3'],
    'Z': ['2', '7'],
    '2': ['Z', '7'],
    'G': ['6', 'C'],
    '6': ['G', 'b'],
    'rn': ['m'],
    'nn': ['m'],
    'vv': ['w'],
    'cl': ['d'],
    'cI': ['d'],
    # Cyrillic lookalikes
    'а': ['a'],  # Cyrillic а
    'е': ['e'],  # Cyrillic е
    'о': ['o'],  # Cyrillic о
    'р': ['p'],  # Cyrillic р
    'с': ['c'],  # Cyrillic с
    'і': ['i'],  # Cyrillic і
}


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def correct_ocr_text(text: str) -> str:
    """
    Correct a single word/token using dictionary lookup.
    
    Args:
        text: OCR output text (single word)
        
    Returns:
        Corrected text, or original if no correction found
    """
    if not text:
        return text
    
    # Try exact match (case-insensitive)
    lower = text.lower()
    if lower in OCR_CORRECTIONS:
        correction = OCR_CORRECTIONS[lower]
        # Preserve original case pattern
        if text.isupper():
            return correction.upper()
        elif text[0].isupper():
            return correction.capitalize()
        return correction
    
    return text


def fuzzy_match_food(text: str, threshold: float = 0.75) -> Optional[str]:
    """
    Find closest match in food vocabulary using fuzzy matching.
    
    Args:
        text: OCR output text
        threshold: Minimum similarity ratio (0-1)
        
    Returns:
        Best matching food word, or None if no good match
    """
    if not text or len(text) < 3:
        return None
    
    lower = text.lower()
    
    # Skip if already a valid word
    if lower in FOOD_VOCABULARY:
        return None
    
    best_match = None
    best_ratio = threshold
    
    for word in FOOD_VOCABULARY:
        # Only compare words of similar length
        if abs(len(word) - len(lower)) > 3:
            continue
            
        ratio = SequenceMatcher(None, lower, word).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = word
    
    return best_match


def correct_menu_item(text: str, use_fuzzy: bool = True) -> str:
    """
    Correct an entire menu item string (multiple words).
    
    Args:
        text: Full menu item text (e.g., "Grllled Chlcken Sandwlch")
        use_fuzzy: Whether to use fuzzy matching for unknown words
        
    Returns:
        Corrected text
    """
    if not text:
        return text
    
    # Split into words, preserving punctuation
    words = re.findall(r'\b\w+\b|\S', text)
    corrected = []
    
    for word in words:
        # Skip punctuation and numbers
        if not word.isalpha():
            corrected.append(word)
            continue
        
        # Layer 1: Dictionary lookup
        fixed = correct_ocr_text(word)
        if fixed != word:
            corrected.append(fixed)
            continue
        
        # Layer 2: Fuzzy matching (optional)
        if use_fuzzy:
            fuzzy = fuzzy_match_food(word)
            if fuzzy:
                # Preserve case
                if word.isupper():
                    corrected.append(fuzzy.upper())
                elif word[0].isupper():
                    corrected.append(fuzzy.capitalize())
                else:
                    corrected.append(fuzzy)
                continue
        
        # No correction found
        corrected.append(word)
    
    # Reconstruct with proper spacing
    result = []
    for i, word in enumerate(corrected):
        if i > 0 and word.isalnum() and corrected[i-1].isalnum():
            result.append(' ')
        result.append(word)
    
    return ''.join(result)


def batch_correct(items: List[str]) -> List[Tuple[str, str, bool]]:
    """
    Correct multiple menu items, tracking what changed.
    
    Args:
        items: List of menu item strings
        
    Returns:
        List of (original, corrected, was_changed) tuples
    """
    results = []
    for item in items:
        corrected = correct_menu_item(item)
        changed = corrected != item
        results.append((item, corrected, changed))
    return results


def add_correction(error: str, correction: str) -> None:
    """
    Add a custom correction to the dictionary.
    
    Args:
        error: The OCR error text
        correction: The correct text
    """
    OCR_CORRECTIONS[error.lower()] = correction.lower()


def add_food_word(word: str) -> None:
    """
    Add a word to the food vocabulary.
    
    Args:
        word: Food word to add
    """
    FOOD_VOCABULARY.add(word.lower())


def get_stats() -> Dict[str, int]:
    """Get statistics about the correction data."""
    return {
        'dictionary_entries': len(OCR_CORRECTIONS),
        'vocabulary_words': len(FOOD_VOCABULARY),
    }


# =============================================================================
# TEST / DEMO
# =============================================================================

if __name__ == '__main__':
    print("Menu OCR Corrections Module")
    print("=" * 50)
    print(f"Dictionary entries: {len(OCR_CORRECTIONS)}")
    print(f"Vocabulary words: {len(FOOD_VOCABULARY)}")
    print()
    
    # Test cases
    test_items = [
        "Grllled Chlcken Sandwlch",
        "PEPPERONІ PІZZA",
        "Fresh Sesfoco Salаd",
        "Crіspy Frіed Shrіmp",
        "vegetarlan burgеr",
        "Smokеd Salnon",
        "APPETLZERS",
        "мedium coffее",
    ]
    
    print("Test Corrections:")
    print("-" * 50)
    for item in test_items:
        corrected = correct_menu_item(item)
        changed = "✓" if corrected != item else " "
        print(f"{changed} {item:30} → {corrected}")