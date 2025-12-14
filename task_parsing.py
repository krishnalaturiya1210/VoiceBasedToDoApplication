# task_parsing.py
"""
Utilities for parsing natural language "add task" commands into
structured attributes: name, priority, category, and due date.

The main entry point is parse_add_task_command(text: str) -> Dict[str, Any].
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from dateutil import parser as date_parser

# Optional: import joblib and try to load the ML models
try:
    from joblib import load
except ImportError:
    # Keep a fallback so the file still imports even if joblib is not installed
    load = None

# Mapping from textual priority labels to numeric levels
PRIORITY_MAP = {
    "low": 1,
    "medium": 2,
    "high": 3,
}

# Global model handles (best-effort load; safe if missing)
PRIORITY_MODEL = None
CATEGORY_MODEL = None

if load is not None:
    try:
        PRIORITY_MODEL = load("priority_model.joblib")
        print("Loaded priority_model.joblib")
    except Exception:
        PRIORITY_MODEL = None

    try:
        CATEGORY_MODEL = load("category_model.joblib")
        print("Loaded category_model.joblib")
    except Exception:
        CATEGORY_MODEL = None


def parse_add_task_command(text: str) -> Dict[str, Any]:
    """
    Parse a raw command such as:
        "add submit HCI report with high priority by monday in school category"

    Returns a dictionary with:
        - name (cleaned task name)
        - priority (1=low, 2=medium, 3=high)
        - category (string, default 'general')
        - due_date (datetime or None)

    Hybrid strategy:
        1) Use rule-based regex patterns to extract and clean attributes.
        2) If priority/category are not found, fall back to small ML models.
    """
    original = text.strip()
    clean = original

    # ---------------------------------------
    # 1. Remove leading helper phrases
    # ---------------------------------------
    start_patterns = [
        r"^i need to add\s+",
        r"^i need to\s+",
        r"^i have to\s+",
        r"^please add\s+",
        r"^please\s+",
        r"^can you add\s+",
        r"^could you add\s+",
        r"^can you\s+",
        r"^could you\s+",
        r"^remind me to\s+",
        r"^remind me\s+",
        r"^add\s+",
        r"^create\s+",
        r"^make\s+",
    ]
    for pat in start_patterns:
        clean = re.sub(pat, "", clean, flags=re.IGNORECASE).strip()

    # Defaults
    priority = 1          # default low
    category = "general"  # default category
    due_date: Optional[datetime] = None

    lower = clean.lower()

    # ---------------------------------------
    # 2. Priority detection (rules)
    # ---------------------------------------
    priority_from_rules = False

    if any(w in lower for w in ["high priority", "urgent", "very important"]):
        priority = 3
        priority_from_rules = True
        clean = re.sub(r"with high priority", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"high priority", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"urgent", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"very important", "", clean, flags=re.IGNORECASE)
    elif "medium priority" in lower:
        priority = 2
        priority_from_rules = True
        clean = re.sub(r"with medium priority", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"medium priority", "", clean, flags=re.IGNORECASE)
    elif "low priority" in lower:
        priority = 1
        priority_from_rules = True
        clean = re.sub(r"with low priority", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"low priority", "", clean, flags=re.IGNORECASE)

    clean = clean.strip()
    lower = clean.lower()

    # ---------------------------------------
    # 3. Category detection (rules)
    # ---------------------------------------
    category_from_rules = False

    cat_match = re.search(r"in ([\w\s]+?) category", clean, flags=re.IGNORECASE)
    if cat_match:
        category = cat_match.group(1).strip()
        category_from_rules = True
        clean = re.sub(
            r"in " + re.escape(cat_match.group(1)) + r" category",
            "",
            clean,
            flags=re.IGNORECASE,
        ).strip()

    clean = clean.strip()
    lower = clean.lower()

    # ---------------------------------------
    # 4. Due date detection
    # ---------------------------------------

    # 4a. Relative phrases with "by ..."
    relative_match = re.search(
        r"\bby\s+(today|tomorrow|tonight|this evening|this afternoon|next week)\b",
        lower,
    )

    if relative_match:
        keyword = relative_match.group(1)
        now = datetime.now()

        if keyword == "today":
            due_date = now
        elif keyword == "tomorrow":
            due_date = now + timedelta(days=1)
        elif keyword in ("tonight", "this evening"):
            due_date = now.replace(hour=20, minute=0, second=0, microsecond=0)
        elif keyword == "this afternoon":
            due_date = now.replace(hour=15, minute=0, second=0, microsecond=0)
        elif keyword == "next week":
            due_date = now + timedelta(weeks=1)

        # Remove the "by <keyword>" part from the text
        clean = re.sub(
            r"\bby\s+" + re.escape(keyword) + r"\b",
            "",
            clean,
            flags=re.IGNORECASE,
        ).strip()
        lower = clean.lower()

    # 4b. General "by <something>" phrase → dateutil.parse
    if due_date is None:
        date_match = re.search(r"\bby\s+(.+)$", lower)
        if date_match:
            date_string = date_match.group(1).strip()
            try:
                parsed_dt = date_parser.parse(date_string, fuzzy=True)
                due_date = parsed_dt
            except Exception:
                pass

    # If we successfully set a due_date at any point,
    # strip everything from 'by' onward to avoid "by monday" in the task name.
    if due_date is not None:
        clean = re.sub(r"\bby\b.*$", "", clean, flags=re.IGNORECASE).strip()
        lower = clean.lower()

    # 4c. Standalone relative words like "tomorrow", "today" (if not already handled)
    if due_date is None:
        lower_original = original.lower()
        now = datetime.now()

        if " tomorrow" in lower_original or lower_original.startswith("tomorrow"):
            due_date = now + timedelta(days=1)
        elif " today" in lower_original or lower_original.startswith("today"):
            due_date = now

    # Also remove standalone time words from the task name
    time_words = [
        r"tomorrow",
        r"today",
        r"tonight",
        r"this evening",
        r"this afternoon",
        r"next week",
        r"next month",
        r"monday",
        r"tuesday",
        r"wednesday",
        r"thursday",
        r"friday",
        r"saturday",
        r"sunday",
    ]
    for w in time_words:
        clean = re.sub(r"\b" + w + r"\b", "", clean, flags=re.IGNORECASE)

    clean = clean.strip()
    lower = clean.lower()

    # ---------------------------------------
    # 5. ML fallbacks for priority / category
    # ---------------------------------------
    if not priority_from_rules and PRIORITY_MODEL is not None:
        try:
            pred_priority = PRIORITY_MODEL.predict([original])[0]
            mapped = PRIORITY_MAP.get(pred_priority.lower())
            if mapped is not None:
                priority = mapped
        except Exception:
            # If model prediction fails, keep the current priority
            pass

    if not category_from_rules and CATEGORY_MODEL is not None:
        try:
            pred_category = CATEGORY_MODEL.predict([original])[0]
            category = pred_category
        except Exception:
            # If model prediction fails, keep the current category
            pass

    # ---------------------------------------
    # 6. Final cleanup of the task name
    # ---------------------------------------
    clean = re.sub(r"\s+", " ", clean).strip(" ,.")
    if not clean:
        clean = original.strip()

    return {
        "name": clean,
        "priority": priority,
        "category": category,
        "due_date": due_date,
    }
