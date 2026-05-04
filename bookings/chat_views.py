"""
AI Chat backend — Phase 1
Capabilities: greet, FAQ, availability, create booking, cancel/hold/restore,
revenue/pending/bookings queries, Hindi+English autocorrect.
"""
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum

from .models import *
from .pricing import calculate_price, check_overlap



def _tokens(text):
    """Get clean tokens for matching."""
    return set(re.findall(r"[a-zA-Z]+", normalize(text)))


def _similarity(a, b):
    """Jaccard similarity 0..1 of two strings via token overlap."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb: return 0
    return len(ta & tb) / len(ta | tb)


def fuzzy_match_memory(user, text, threshold=0.55):
    """Find a similar past query that succeeded; return its intent or None."""
    norm = normalize(text)
    # Exact match first
    exact = ChatMemory.objects.filter(owner=user, normalized=norm).first()
    if exact:
        ChatMemory.objects.filter(id=exact.id).update(
            use_count=exact.use_count + 1
        )
        return exact.intent
    # Fuzzy match — check last 100 memories
    candidates = ChatMemory.objects.filter(owner=user).order_by('-use_count', '-last_used_at')[:100]
    best, best_score = None, 0
    for c in candidates:
        s = _similarity(norm, c.normalized)
        if s > best_score:
            best_score = s
            best = c
    if best and best_score >= threshold:
        return best.intent
    return None


def remember_query(user, text, intent):
    """Save successful query for future fuzzy matching."""
    if intent in ('unknown', 'yes', 'no'):
        return
    norm = normalize(text)
    if not norm or len(norm) < 3:
        return
    obj, created = ChatMemory.objects.get_or_create(
        owner=user, normalized=norm,
        defaults={'query_text': text[:200], 'intent': intent}
    )
    if not created:
        ChatMemory.objects.filter(id=obj.id).update(use_count=obj.use_count + 1)


# ── Token-based intent fallback ──
INTENT_TOKEN_HINTS = {
    'revenue_today':      {'revenue', 'kamai', 'income', 'paisa', 'earning', 'today', 'aaj'},
    'revenue_yesterday':  {'revenue', 'kamai', 'yesterday', 'kal'},
    'revenue_week':       {'revenue', 'kamai', 'week', 'hafte'},
    'revenue_month':      {'revenue', 'kamai', 'month', 'mahine'},
    'pending_today':      {'pending', 'due', 'baki', 'lena', 'today', 'aaj'},
    'pending_all':        {'pending', 'due', 'baki', 'total', 'sab'},
    'pending_week':       {'pending', 'due', 'week', 'hafte'},
    'pending_month':      {'pending', 'due', 'month', 'mahine'},
    'received_today':     {'received', 'collected', 'aaya', 'aagya', 'paid', 'aa', 'gaya', 'today', 'aaj', 'mil'},
    'received_yesterday': {'received', 'aaya', 'kal', 'yesterday'},
    'received_week':      {'received', 'aaya', 'week', 'hafte'},
    'received_month':     {'received', 'aaya', 'month', 'mahine'},
    'bookings_today':     {'booking', 'bookings', 'today', 'aaj', 'kitne'},
    'top_customers':      {'top', 'best', 'customer', 'customers'},
    'total_customers':    {'total', 'kitne', 'customer', 'customers'},
    'total_turfs':        {'total', 'kitne', 'turf', 'turfs'},
    'check_availability': {'free', 'empty', 'khali', 'available', 'busy', 'booked'},
    'create_booking':     {'book', 'booking', 'create', 'banao', 'reserve'},
    'cancel_booking':     {'cancel'},
    'hold_booking':       {'hold'},
    'complete_booking':   {'complete', 'done'},
    'help':               {'help', 'madad', 'options', 'kya', 'sakte'},
    'greet':              {'hi', 'hello', 'hey', 'namaste', 'namaskar'},
    'close':              {'bye', 'goodbye', 'exit', 'alvida'},
    'thanks':             {'thank', 'thanks', 'shukriya', 'dhanyawad'},
    'faq_create_booking': {'how', 'create', 'booking', 'kaise', 'banao'},
    'faq_pricing':        {'how', 'pricing', 'work', 'kaam', 'karta'},
}


def token_fallback_intent(text):
    """Score user text against known intents by token overlap."""
    user_tokens = _tokens(text)
    if not user_tokens:
        return None
    best, best_score = None, 0
    for intent, keywords in INTENT_TOKEN_HINTS.items():
        score = len(user_tokens & keywords)
        # Need at least 2 matching tokens, OR 1 if it's a unique-keyword intent
        if score >= 2 or (score >= 1 and len(user_tokens) <= 3):
            # Prefer longer overlap, scaled by query length
            ratio = score / max(len(user_tokens), len(keywords))
            if ratio > best_score:
                best_score = ratio
                best = intent
    return best if best_score >= 0.25 else None

# ════════════════════════════════════════════════════════
# LANGUAGE / NORMALIZATION
# ════════════════════════════════════════════════════════

HINDI_NUMS = {
    'ek':1,'do':2,'teen':3,'char':4,'chaar':4,
    'panch':5,'paanch':5,'paach':5,
    'chhe':6,'che':6,'cheh':6,'chha':6,
    'saat':7,'sat':7,'saath':7,
    'aath':8,'ath':8,'nau':9,'no':9,'das':10,'dus':10,
    'gyarah':11,'gyaarah':11,'baarah':12,'barah':12,
}

ORDINALS = {
    'first':1,'second':2,'third':3,'fourth':4,'fifth':5,'sixth':6,'seventh':7,
    'eighth':8,'ninth':9,'tenth':10,'eleventh':11,'twelfth':12,'thirteenth':13,
    'fourteenth':14,'fifteenth':15,'sixteenth':16,'seventeenth':17,
    'eighteenth':18,'nineteenth':19,'twentieth':20,'thirtieth':30,
    'twenty-first':21,'twenty-second':22,'twenty-third':23,'twenty-fourth':24,
    'twenty-fifth':25,'twenty-sixth':26,'twenty-seventh':27,'twenty-eighth':28,
    'twenty-ninth':29,'thirty-first':31,
}

# Common typo / transliteration fixes
TYPO_FIXES = {
    r'\bbookng\b': 'booking',
    r'\bbookin\b': 'booking',
    r'\bbookng\b': 'booking',
    r'\bturff\b': 'turf',
    r'\btruf\b': 'turf',
    r'\bcustmer\b': 'customer',
    r'\bcustomr\b': 'customer',
    r'\brevnue\b': 'revenue',
    r'\brevenu\b': 'revenue',
    r'\bpendng\b': 'pending',
    r'\btoday\'?s\b': 'today',
    r'\btomorrw\b': 'tomorrow',
    r'\btmrw\b': 'tomorrow',
    r'\bmrw\b': 'tomorrow',
    r'\bavailbl\b': 'available',
    r'\bfre\b': 'free',
    r'\bplz\b': 'please',
    r'\bpls\b': 'please',
    r'\baj\b': 'aaj',
    r'\bkll\b': 'kal',
    r'\bbje\b': 'baje',
    r'\bnaam\b': 'name',
    r'\bnam\b': 'name',
    r'\bkaha\b': 'kahan',
}

HINGLISH_MARKERS = {
    'hai','hain','kya','kaise','kaisa','kitna','kitne','kitni','kaun','kaunsa',
    'kahan','kab','kyun','mujhe','mera','meri','aap','aapka','aapki','main','hum',
    'aaj','aj','kal','parso','abhi','baad','pehle','subah','shaam','sham','raat',
    'din','baje','bajke','somvaar','ravivaar',
    'banao','banana','bana','karo','krde','krdo','krna','dikhao','dikha','dedo',
    'batao','bata','dekho','chahiye','chahta','hoga','hogi','khali','khaali',
    'bhari','baki','bakaya','lena','dena','paisa','kamai','kamaya',
    'kuch','sab','thoda','jyada','zyada','bahut','aur','ya','lekin','phir','toh',
    'namaste','namaskar','shukriya','dhanyawad','alvida',
    'ek','do','teen','char','chaar','panch','paanch','chhe','che',
    'saat','aath','nau','das','haan','haa','nahi','nahin','ji',
    'cancel','hold','restore','complete',
}


def normalize(text):
    """Lowercase + fix typos + hindi numbers + ordinals."""
    out = text.lower().strip()

    # Fix common typos / shortforms
    for pat, rep in TYPO_FIXES.items():
        out = re.sub(pat, rep, out)

    # Hindi numbers → digits (longest first)
    for word in sorted(HINDI_NUMS, key=len, reverse=True):
        out = re.sub(r'\b' + word + r'\b', str(HINDI_NUMS[word]), out)

    # English ordinals
    for word in sorted(ORDINALS, key=len, reverse=True):
        out = re.sub(r'\b' + word + r'\b', str(ORDINALS[word]), out)

    # "1st" / "2nd" / "3rd" → digits
    out = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', r'\1', out)

    # Common substitutions
    out = re.sub(r'\bse\b', 'to', out)
    out = re.sub(r'\btak\b', 'to', out)
    out = re.sub(r'\bbaje\b', '', out)
    out = re.sub(r'\bbajke\b', '', out)
    out = re.sub(r"\bo'?clock\b", '', out)
    out = re.sub(r'\bmain\b', 'may', out)   # "1 of main" → "1 of may"
    out = re.sub(r'\s+', ' ', out).strip()

    return out


def detect_lang(raw_text):
    """Hindi/Hinglish → 'hi'. English → 'en'."""
    if re.search(r'[\u0900-\u097F]', raw_text):
        return 'hi'
    words = re.findall(r"[a-zA-Z']+", raw_text.lower())
    if not words:
        return 'en'
    hi_count = sum(1 for w in words if w in HINGLISH_MARKERS)
    if hi_count >= 2: return 'hi'
    if hi_count >= 1 and (hi_count / len(words)) >= 0.20: return 'hi'
    return 'en'


def R(en, hi=None):
    return {'en': en, 'hi': hi or en}


def pick(r, lang):
    return r['hi'] if lang == 'hi' else r['en']


# ════════════════════════════════════════════════════════
# PARSERS
# ════════════════════════════════════════════════════════

def parse_phone(text):
    m = re.search(r'\b(\d{10})\b', text)
    return m.group(1) if m else None


def parse_amount(text):
    m = re.search(r'(?:rupees?|rs\.?|₹|inr|for|amount)\s*(\d+)', text)
    return int(m.group(1)) if m else None


def parse_date(text):
    t = normalize(text)
    today = timezone.localtime().date()

    if re.search(r'\b(today|aaj|abhi)\b', t): return today
    if re.search(r'\b(tomorrow|kal)\b', t): return today + timedelta(days=1)
    if re.search(r'\b(day after tomorrow|parso|parsoon)\b', t): return today + timedelta(days=2)

    days = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
    for i, d in enumerate(days):
        if re.search(r'\b' + d + r'\b', t):
            delta = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=delta)

    months = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
              'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
              'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

    def make(d_n, m_n):
        try:
            d = date(today.year, m_n, d_n)
            if d < today: d = date(today.year + 1, m_n, d_n)
            return d
        except ValueError:
            return None

    # "1 may" / "1 of may"
    m = re.search(r'\b(\d{1,2})\s+(?:of\s+)?(\w+)\b', t)
    if m and m.group(2) in months:
        d = make(int(m.group(1)), months[m.group(2)])
        if d: return d

    # "may 1"
    m = re.search(r'\b(\w+)\s+(\d{1,2})\b', t)
    if m and m.group(1) in months:
        d = make(int(m.group(2)), months[m.group(1)])
        if d: return d

    # DD/MM or DD-MM
    m = re.search(r'\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b', t)
    if m:
        try:
            d_n, m_n = int(m.group(1)), int(m.group(2))
            year = int(m.group(3) or today.year)
            if year < 100: year += 2000
            d = date(year, m_n, d_n)
            return d
        except ValueError:
            pass

    return None


def parse_time_range(text):
    t = normalize(text)

    def norm(h, p):
        h = int(h)
        if p == 'pm' and h < 12: h += 12
        if p == 'am' and h == 12: h = 0
        if not p and 1 <= h <= 7: h += 12
        return h

    # "21:00 to 23:00"
    m = re.search(r'(\d{1,2}):(\d{2})\s*to\s*(\d{1,2}):(\d{2})', t)
    if m:
        return (f"{int(m.group(1)):02d}:{m.group(2)}",
                f"{int(m.group(3)):02d}:{m.group(4)}")

    # "6 pm to 8 pm" / "6 to 8 pm"
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*to\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', t)
    if m:
        h1, mn1, p1, h2, mn2, p2 = m.groups()
        mn1 = mn1 or '00'; mn2 = mn2 or '00'
        p1 = p1 or p2
        p2 = p2 or p1
        return (f"{norm(h1,p1):02d}:{mn1}", f"{norm(h2,p2):02d}:{mn2}")

    return (None, None)

def fmt_time(time_str):
    """'19:00' → '7:00 PM', '06:30' → '6:30 AM'."""
    if not time_str: return ''
    try:
        t = datetime.strptime(time_str, '%H:%M')
        return t.strftime('%I:%M %p').lstrip('0')
    except ValueError:
        return time_str

def parse_single_time(text):
    t = normalize(text)
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', t)
    if m:
        h = int(m.group(1)); mn = m.group(2) or '00'; p = m.group(3)
        if p == 'pm' and h < 12: h += 12
        if p == 'am' and h == 12: h = 0
        if not p and 1 <= h <= 7: h += 12
        return f"{h:02d}:{mn}"
    return None


def find_turf(user, text):
    t = text.lower()
    turfs = list(Turf.objects.filter(owner=user, is_active=True))
    for turf in turfs:
        if turf.name.lower() in t:
            return turf
    for turf in turfs:
        for word in turf.name.lower().split():
            if len(word) > 3 and word in t:
                return turf
    return None


def parse_customer_name(text):
    m = re.search(r'(?:for|naam|name(?:\s+is)?|customer|client|guest)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)', text, re.IGNORECASE)
    if m:
        n = m.group(1).strip()
        if n.lower() in ['the','a','an','my','today','tomorrow','his','her']: return None
        return n.title()
    return None


# ════════════════════════════════════════════════════════
# INTENT DETECTION
# ════════════════════════════════════════════════════════

def detect_intent(text):
    t = normalize(text)

    if re.fullmatch(r'\s*(yes|yeah|yep|ok|okay|haan|haa|ji|sure|kar do|book karo|do it|book it)\s*\.?\s*', t):
        return 'yes'
    if re.fullmatch(r'\s*(no|nope|nahi|nahin|cancel|don\'?t)\s*\.?\s*', t):
        return 'no'

    if re.search(r'\b(hi|hello|hey|hii|namaste|namaskar|hola|good morning|good evening)\b', t):
        return 'greet'
    if re.search(r'\b(bye|goodbye|exit|alvida|band karo|stop)\b', t):
        return 'close'
    if re.search(r'\b(thank|thanks|shukriya|dhanyawad)\b', t):
        return 'thanks'

    # FAQ matchers
    if re.search(r'\bhow\s+(?:to|do\s+i)\s+create.*booking\b', t) or \
       re.search(r'\bbooking\s+kaise\s+banao?\b', t):
        return 'faq_create_booking'
    if re.search(r'\bhow\s+(?:does\s+)?(?:dynamic\s+)?pricing\b', t) or \
       re.search(r'\bpricing\s+kaise\s+kaam\b', t):
        return 'faq_pricing'
    if re.search(r'\bhow\s+to\s+add.*turf\b', t) or re.search(r'\bturf\s+add\s+kaise\b', t):
        return 'faq_add_turf'
    if re.search(r'\b(?:overnight|raat\s+bhar|midnight)\b', t):
        return 'faq_overnight'
    if re.search(r'\bhow\s+to\s+edit.*booking\b', t):
        return 'faq_edit_booking'

    # Cancel/hold/restore booking
    if re.search(r'\bcancel\b', t) and re.search(r'\bbooking\b', t):
        return 'cancel_booking'
    if re.search(r'\bhold\b', t) and re.search(r'\bbooking\b', t):
        return 'hold_booking'
    if re.search(r'\b(complete|done)\b', t) and re.search(r'\bbooking\b', t):
        return 'complete_booking'

    # Booking creation
    if re.search(r'\b(book|booking|create|reserve|banao|bana\s+do|banana)\b', t) and \
       not re.search(r'\b(how|kaise)\b', t):
        return 'create_booking'

    # Availability
    if re.search(r'\b(empty|free|available|khali|khaali|busy|booked)\b', t):
        return 'check_availability'

    # Revenue
    if re.search(r'\b(revenue|earning|kamai|kamaya|income|paisa)\b', t):
        if re.search(r'\b(yesterday|kal)\b', t): return 'revenue_yesterday'
        if re.search(r'\b(week|hafte)\b', t): return 'revenue_week'
        if re.search(r'\b(month|mahine)\b', t): return 'revenue_month'
        return 'revenue_today'

    # Pending
    if re.search(r'\b(pending|due|baki|udhaar|unpaid|lena|bakaya)\b', t):
        if re.search(r'\b(today|aaj)\b', t): return 'pending_today'
        if re.search(r'\b(week|hafte)\b', t): return 'pending_week'
        if re.search(r'\b(month|mahine)\b', t): return 'pending_month'
        return 'pending_all'


    # Received / collected (paid amount)
    if re.search(r'\b(received|collected|aaya|aagya|aa\s+gaya|mil\s*gaya|paid|paisa\s+aaya)\b', t):
        if re.search(r'\b(today|aaj)\b', t): return 'received_today'
        if re.search(r'\b(yesterday|kal)\b', t): return 'received_yesterday'
        if re.search(r'\b(week|hafte)\b', t): return 'received_week'
        if re.search(r'\b(month|mahine)\b', t): return 'received_month'
        return 'received_today'   # default to today

    # Bookings count
    if re.search(r'\b(today|aaj)\b', t) and re.search(r'\bbooking', t):
        return 'bookings_today'
    if re.search(r'\b(show|list|dikhao)\b', t) and re.search(r'\bbooking', t):
        return 'bookings_today'
    if re.search(r'\b(how many|kitne|total)\b', t) and re.search(r'\bbooking', t):
        return 'bookings_today'

    # Customers / Turfs
    if re.search(r'\btop\b', t) and re.search(r'\bcustomer', t):
        return 'top_customers'
    if re.search(r'\b(how many|kitne|total)\b', t) and re.search(r'\bcustomer', t):
        return 'total_customers'
    if re.search(r'\b(how many|kitne|total)\b', t) and re.search(r'\bturf', t):
        return 'total_turfs'

    if re.search(r'\b(help|kya kar sakte|what can you do|madad|capabilities)\b', t):
        return 'help'
    
    # ⭐ Show/view/find a customer's bookings (PRIORITY before create)
    if re.search(r'\b(show|view|find|see|list|dikhao|dekhao|batao|search)\b', t) and \
       re.search(r'\b(booking|bookings)\b', t):
        return 'show_customer_bookings'
    if re.search(r'\b(show|view|find|see|dikhao|dekhao|batao|search)\b', t) and \
       re.search(r'\b(customer|client|guest)\b', t):
        return 'show_customer_bookings'

    # Edit / update booking amount or payment
    if re.search(r'\b(update|edit|change|modify|set|update|fix|badlo|change\s+karo)\b', t):
        if re.search(r'\b(amount|price|total|paisa|payment|advance|paid)\b', t):
            return 'update_booking_amount'

    # Add payment to existing booking
    if re.search(r'\b(advance|payment|paid|paisa\s+aaya|add)\b', t) and \
       re.search(r'\b(\d+)\b', t) and \
       not re.search(r'\b(book|booking|create|banao)\b', t):
        return 'add_payment'

    # Booking creation (last priority)
    if re.search(r'\b(book|booking|create|reserve|banao|bana\s+do|banana)\b', t) and \
       not re.search(r'\b(how|kaise|show|view|find|dikhao)\b', t):
        return 'create_booking'

    return 'unknown'


# ════════════════════════════════════════════════════════
# HANDLERS — Simple
# ════════════════════════════════════════════════════════

def handle_greet(user, lang):
    name = user.first_name or user.username
    return {'reply': pick(R(
        f"Hi {name}! I can help with bookings, availability, payments, and more. Try 'show today revenue' or 'is round turf free tomorrow 6 PM'.",
        f"Namaste {name}! Main bookings, availability, payments mein madad kar sakta hoon. Try kariye 'aaj ka revenue' ya 'round turf kal 6 baje free hai?'."
    ), lang)}


def handle_close(lang):
    return {'reply': pick(R("Goodbye! Tap the chat anytime.", "Alvida! Phir milte hain."), lang),
            'close': True}


def handle_thanks(lang):
    return {'reply': pick(R("Welcome!", "Aapka swagat hai!"), lang)}


def handle_help(lang):
    en = ("I can help with:\n"
          "• Booking — 'book round turf tomorrow 6-8 PM for Samay 9999988888'\n"
          "• Availability — 'is round turf free at 6 PM'\n"
          "• Cancel/Hold — 'cancel booking 12'\n"
          "• Revenue — 'today's revenue', 'this week revenue'\n"
          "• Pending — 'pending today', 'total pending'\n"
          "• FAQs — 'how to create booking', 'how does pricing work'\n"
          "Type bye to close.")
    hi = ("Main ye sab kar sakta hoon:\n"
          "• Booking — 'kal 6 se 8 baje round turf book karo Samay 9999988888'\n"
          "• Availability — 'round turf 6 baje khali hai?'\n"
          "• Cancel/Hold — 'booking 12 cancel karo'\n"
          "• Revenue — 'aaj ka revenue', 'is hafte ki kamai'\n"
          "• Pending — 'aaj ka pending', 'total pending'\n"
          "• FAQ — 'booking kaise banao', 'pricing kaise kaam karta'\n"
          "Bye type karke close kar sakte ho.")
    return {'reply': pick(R(en, hi), lang)}


def handle_unknown(text, lang):
    return {'reply': pick(R(
        "Hmm, not sure about that. I can help with revenue, pending dues, bookings, availability, and creating bookings. What would you like?",
        "Hmm, ye samajh nahi aaya. Main revenue, pending, bookings, availability aur booking banane mein madad kar sakta hoon. Kya chahiye?"
    ), lang)}


# FAQs
def faq_create_booking(lang):
    en = ("To create a booking:\n"
          "1. Tap 'New Booking' from sidebar/bottom nav\n"
          "2. Select turf and date\n"
          "3. Pick start and end time slots\n"
          "4. Enter customer phone (auto-fills if returning)\n"
          "5. Confirm price and payment\n"
          "Or just say: 'book round turf tomorrow 6 PM to 8 PM for Samay 9999988888'")
    hi = ("Booking banane ke liye:\n"
          "1. Sidebar/bottom nav se 'New Booking' tap karo\n"
          "2. Turf aur date select karo\n"
          "3. Start aur end time slots pick karo\n"
          "4. Customer phone enter karo (returning ho to auto-fill)\n"
          "5. Price aur payment confirm karo\n"
          "Ya seedha bolo: 'kal 6 se 8 baje round turf book karo Samay 9999988888'")
    return {'reply': pick(R(en, hi), lang)}


def faq_pricing(lang):
    en = ("Pricing rules priority:\n"
          "1. Specific Date (e.g. holiday) — highest\n"
          "2. Day of Week (e.g. Saturday)\n"
          "3. Time-only rule (any day)\n"
          "4. Default turf rate — fallback\n"
          "Each rule can be Fixed ₹/hr or a Multiplier of default. Overnight rules (5 PM-6 AM) work too.")
    hi = ("Pricing rules priority:\n"
          "1. Specific Date (jaise holiday) — top\n"
          "2. Day of Week (jaise Saturday)\n"
          "3. Time-only rule (any day)\n"
          "4. Default turf rate — fallback\n"
          "Har rule ya to Fixed ₹/hr ho sakti hai ya Multiplier. Overnight rules (5 PM-6 AM) bhi chalti hain.")
    return {'reply': pick(R(en, hi), lang)}


def faq_add_turf(lang):
    return {'reply': pick(R(
        "Go to Settings → My Turfs → Add Turf. Set name, location, sport, hours, and default ₹/hr.",
        "Settings → My Turfs → Add Turf pe jao. Naam, location, sport, hours, aur default ₹/hr set karo."
    ), lang)}


def faq_overnight(lang):
    return {'reply': pick(R(
        "Overnight bookings (e.g. 11 PM to 1 AM next day) are supported. Set start time after end time. Pricing rules from 5 PM to 6 AM also work overnight.",
        "Overnight bookings (jaise 11 PM se 1 AM agle din) supported hain. Start time end ke baad rakho. 5 PM-6 AM ki pricing rules bhi overnight chalti hain."
    ), lang)}


def faq_edit_booking(lang):
    return {'reply': pick(R(
        "Open booking detail page → tap Edit (top right). You can change date, time, and amount. If new time conflicts, alternatives are suggested.",
        "Booking detail page kholo → Edit tap karo (top right). Date, time, amount change kar sakte ho. Conflict ho to alternatives suggest hote hain."
    ), lang)}


# ════════════════════════════════════════════════════════
# DATA HANDLERS
# ════════════════════════════════════════════════════════

def _rev_in(user, df, dt):
    bks = Booking.objects.filter(owner=user, booking_date__gte=df, booking_date__lte=dt,
                                  status__in=['booked','completed'])
    return bks.count(), int(bks.aggregate(t=Sum('paid_amount'))['t'] or 0)


def handle_revenue_today(user, lang):
    today = timezone.localtime().date()
    c, r = _rev_in(user, today, today)
    if r == 0:
        return {'reply': pick(R("No revenue today yet.", "Aaj abhi koi revenue nahi."), lang)}
    return {'reply': pick(R(f"Today: ₹{r} from {c} bookings.",
                            f"Aaj: ₹{r}, {c} bookings se."), lang)}


def handle_revenue_yesterday(user, lang):
    today = timezone.localtime().date()
    y = today - timedelta(days=1)
    c, r = _rev_in(user, y, y)
    return {'reply': pick(R(f"Yesterday: ₹{r} from {c} bookings.",
                            f"Kal: ₹{r}, {c} bookings se."), lang)}


def handle_revenue_week(user, lang):
    today = timezone.localtime().date()
    ws = today - timedelta(days=today.weekday())
    c, r = _rev_in(user, ws, today)
    return {'reply': pick(R(f"This week: ₹{r} from {c} bookings.",
                            f"Is hafte: ₹{r}, {c} bookings se."), lang)}


def handle_revenue_month(user, lang):
    today = timezone.localtime().date()
    ms = today.replace(day=1)
    c, r = _rev_in(user, ms, today)
    return {'reply': pick(R(f"This month: ₹{r} from {c} bookings.",
                            f"Is mahine: ₹{r}, {c} bookings se."), lang)}


def _pending_in(user, df=None, dt=None):
    qs = Booking.objects.filter(owner=user, status='booked',
                                  payment_status__in=['pending','partial'])
    if df: qs = qs.filter(booking_date__gte=df)
    if dt: qs = qs.filter(booking_date__lte=dt)
    return qs.count(), int(sum(b.remaining_amount for b in qs))


def handle_pending_today(user, lang):
    today = timezone.localtime().date()
    c, t = _pending_in(user, today, today)
    if c == 0:
        return {'reply': pick(R("No pending today.", "Aaj koi pending nahi."), lang)}
    return {'reply': pick(R(f"Today: ₹{t} pending from {c} bookings.",
                            f"Aaj: ₹{t} pending, {c} bookings se."), lang)}


def handle_pending_week(user, lang):
    today = timezone.localtime().date()
    ws = today - timedelta(days=today.weekday())
    c, t = _pending_in(user, ws, today)
    return {'reply': pick(R(f"This week: ₹{t} pending from {c} bookings.",
                            f"Is hafte: ₹{t} pending, {c} bookings se."), lang)}


def handle_pending_month(user, lang):
    today = timezone.localtime().date()
    ms = today.replace(day=1)
    c, t = _pending_in(user, ms, today)
    return {'reply': pick(R(f"This month: ₹{t} pending from {c} bookings.",
                            f"Is mahine: ₹{t} pending, {c} bookings se."), lang)}


def handle_pending_all(user, lang):
    c, t = _pending_in(user)
    if c == 0:
        return {'reply': pick(R("No pending dues. All clear!", "Sab clear hai, koi pending nahi."), lang)}
    return {'reply': pick(R(f"Total ₹{t} pending across {c} bookings.",
                            f"Total ₹{t} pending hai, {c} bookings se."), lang)}


def handle_bookings_today(user, lang):
    today = timezone.localtime().date()
    bks = Booking.objects.filter(owner=user, booking_date=today)
    total = bks.count()
    if total == 0:
        return {'reply': pick(R("No bookings today.", "Aaj koi booking nahi."), lang)}
    booked = bks.filter(status='booked').count()
    completed = bks.filter(status='completed').count()
    return {'reply': pick(R(
        f"Today: {total} bookings — {booked} active, {completed} completed.",
        f"Aaj: {total} bookings — {booked} active, {completed} completed."
    ), lang)}


def handle_top_customers(user, lang):
    top = Customer.objects.filter(owner=user).order_by('-total_spent')[:3]
    if not top:
        return {'reply': pick(R("No customers yet.", "Abhi koi customer nahi."), lang)}
    parts = [f"{i}. {c.name} (₹{int(c.total_spent)})" for i, c in enumerate(top, 1)]
    return {'reply': pick(R("Top customers: " + ", ".join(parts),
                            "Top customers: " + ", ".join(parts)), lang)}

def handle_show_customer_bookings(user, text, lang):
    """Find a customer by name or phone, list their bookings."""
    phone = parse_phone(text)
    customer = None

    if phone:
        customer = Customer.objects.filter(owner=user, phone=phone).first()

    if not customer:
        # Try matching by name
        # Strip command words
        cleaned = re.sub(r'\b(show|view|find|see|list|dikhao|dekhao|batao|search|booking|bookings|customer|client|guest|of|ki|ka|ke|the|a|an)\b', '', text.lower(), flags=re.IGNORECASE)
        cleaned = re.sub(r'\d+', '', cleaned).strip()
        if len(cleaned) >= 2:
            # Look up by name (case-insensitive contains)
            customer = Customer.objects.filter(owner=user, name__icontains=cleaned).first()

    if not customer:
        return {'reply': pick(R(
            "Couldn't find that customer. Give me a phone number or exact name.",
            "Customer nahi mila. Phone number ya exact naam batao."
        ), lang)}

    bookings = Booking.objects.filter(owner=user, customer=customer).order_by('-booking_date', '-start_time')[:5]
    if not bookings:
        return {'reply': pick(R(
            f"{customer.name} has no bookings yet.",
            f"{customer.name} ki abhi koi booking nahi."
        ), lang)}

    lines_en = [f"{customer.name} — last {bookings.count()} bookings:"]
    lines_hi = [f"{customer.name} ki last {bookings.count()} bookings:"]
    for b in bookings:
        date_str = b.booking_date.strftime('%d %b')
        t_str = f"{fmt_time(b.start_time.strftime('%H:%M'))}–{fmt_time(b.end_time.strftime('%H:%M'))}"
        due = f", due ₹{int(b.remaining_amount)}" if b.remaining_amount > 0 else ""
        lines_en.append(f"• #{b.id} {date_str} {t_str}, ₹{int(b.total_amount)} ({b.get_status_display()}){due}")
        lines_hi.append(f"• #{b.id} {date_str} {t_str}, ₹{int(b.total_amount)} ({b.get_status_display()}){due}")
    return {'reply': pick(R("\n".join(lines_en), "\n".join(lines_hi)), lang),
            'redirect': f'/customers/{customer.pk}/'}


def handle_total_customers(user, lang):
    c = Customer.objects.filter(owner=user).count()
    return {'reply': pick(R(f"You have {c} customers.", f"Aapke {c} customers hain."), lang)}


def handle_total_turfs(user, lang):
    c = Turf.objects.filter(owner=user, is_active=True).count()
    return {'reply': pick(R(f"You have {c} active turfs.", f"Aapke {c} active turfs hain."), lang)}


# ════════════════════════════════════════════════════════
# AVAILABILITY
# ════════════════════════════════════════════════════════

def _suggest_alts(user, turf, booking_date, st, et):
    duration = (datetime.combine(booking_date, et) - datetime.combine(booking_date, st)).total_seconds() / 60
    if duration <= 0: duration += 1440
    suggestions = []
    for shift in [60, -60, 120, -120, 180]:
        ts = datetime.combine(booking_date, st) + timedelta(minutes=shift)
        te = ts + timedelta(minutes=duration)
        if not check_overlap(turf, ts.date(), ts.time(), te.time()):
            suggestions.append(f"{ts.strftime('%I:%M %p').lstrip('0')}–{te.strftime('%I:%M %p').lstrip('0')}")
        if len(suggestions) >= 2: break
    return suggestions


def handle_check_availability(user, text, lang, prefilled=None):
    state = prefilled or {}

    # Try to fill turf
    if 'turf_id' not in state:
        turf = find_turf(user, text)
        if turf: state['turf_id'] = turf.id
    # Try to fill date
    if 'date' not in state:
        d = parse_date(text)
        if d: state['date'] = d.isoformat()
    # Try to fill time
    if 'start' not in state or 'end' not in state:
        s, e = parse_time_range(text)
        if s and e:
            state['start'] = s
            state['end'] = e

    # ── Step 1: turf missing ──
    if 'turf_id' not in state:
        turfs = Turf.objects.filter(owner=user, is_active=True)
        if not turfs:
            return {'reply': pick(R("Add a turf first.", "Pehle ek turf add karo."), lang)}
        names = ", ".join(t.name for t in turfs[:3])
        return {'reply': pick(R(f"Which turf? Options: {names}.",
                                 f"Kaunsa turf? Options: {names}."), lang),
                'context': {'flow': 'availability', 'state': state}}

    # ── Step 2: time missing ──
    if 'start' not in state or 'end' not in state:
        return {'reply': pick(R("What time? Like '6 PM to 7 PM'.",
                                 "Time kya? Jaise '6 se 7 baje'."), lang),
                'context': {'flow': 'availability', 'state': state}}

    # Date defaults to today
    if 'date' not in state:
        state['date'] = timezone.localtime().date().isoformat()

    # ── Run availability check ──
    turf = Turf.objects.filter(id=state['turf_id'], owner=user).first()
    if not turf:
        return {'reply': pick(R("Turf not found.", "Turf nahi mila."), lang)}

    booking_date = datetime.strptime(state['date'], '%Y-%m-%d').date()
    s, e = state['start'], state['end']
    st = datetime.strptime(s, '%H:%M').time()
    et = datetime.strptime(e, '%H:%M').time()
    busy = check_overlap(turf, booking_date, st, et)

    date_str = booking_date.strftime('%d %B')
    time_str = f"{fmt_time(s)} to {fmt_time(e)}"

    if busy:
        alts = _suggest_alts(user, turf, booking_date, st, et)
        alt_en = " Try " + " or ".join(alts) + "." if alts else ""
        alt_hi = " " + " ya ".join(alts) + " try kariye." if alts else ""
        return {'reply': pick(R(
            f"{turf.name} is busy on {date_str} {time_str}.{alt_en}",
            f"{turf.name} {date_str} ko {time_str} pe booked hai.{alt_hi}"
        ), lang)}

    return {'reply': pick(R(
        f"{turf.name} is FREE on {date_str} from {time_str}. Want to book it?",
        f"{turf.name} {date_str} ko {time_str} pe khali hai! Book karu?"
    ), lang),
    'context': {'flow': 'confirm_book', 'turf_id': turf.id,
                'date': booking_date.isoformat(), 'start': s, 'end': e}}


# ════════════════════════════════════════════════════════
# BOOKING CREATION (multi-turn)
# ════════════════════════════════════════════════════════

def start_booking_flow(user, text, lang, prefilled=None):
    state = prefilled or {}
    if 'turf_id' not in state:
        turf = find_turf(user, text)
        if turf: state['turf_id'] = turf.id
    if 'date' not in state:
        d = parse_date(text)
        if d: state['date'] = d.isoformat()
    if 'start' not in state:
        s, e = parse_time_range(text)
        if s and e: state['start'] = s; state['end'] = e
    if 'phone' not in state:
        p = parse_phone(text)
        if p: state['phone'] = p
    if 'name' not in state:
        n = parse_customer_name(text)
        if n: state['name'] = n
    if 'amount' not in state:
        a = parse_amount(text)
        if a: state['amount'] = a
    return continue_booking_flow(user, state, lang)


def continue_booking_flow(user, state, lang, latest_text=''):
    # Step 1: turf
    if 'turf_id' not in state and latest_text:
        turf = find_turf(user, latest_text)
        if turf: state['turf_id'] = turf.id
    if 'turf_id' not in state:
        turfs = Turf.objects.filter(owner=user, is_active=True)
        if not turfs:
            return {'reply': pick(R("No turfs. Add one first.", "Koi turf nahi. Pehle add karo."), lang)}
        names = ", ".join(t.name for t in turfs[:3])
        return {'reply': pick(R(f"Which turf? Options: {names}.",
                                 f"Kaunsa turf? {names}."), lang),
                'context': {'flow': 'booking', 'state': state}}

    # Step 2: date
    if 'date' not in state and latest_text:
        d = parse_date(latest_text)
        if d: state['date'] = d.isoformat()
    if 'date' not in state:
        return {'reply': pick(R("Which date? Today, tomorrow, or any date.",
                                 "Kaunsi date? Aaj, kal, ya date bataye."), lang),
                'context': {'flow': 'booking', 'state': state}}

    # Step 3: time
    if ('start' not in state or 'end' not in state) and latest_text:
        s, e = parse_time_range(latest_text)
        if s and e: state['start'] = s; state['end'] = e
    if 'start' not in state or 'end' not in state:
        return {'reply': pick(R("What time? Like '6 PM to 8 PM'.",
                                 "Time kya? Jaise '6 se 8 baje'."), lang),
                'context': {'flow': 'booking', 'state': state}}

    # Overlap
    turf = Turf.objects.filter(id=state['turf_id'], owner=user).first()
    if not turf:
        return {'reply': pick(R("Turf not found.", "Turf nahi mila."), lang)}
    bd = datetime.strptime(state['date'], '%Y-%m-%d').date()
    st = datetime.strptime(state['start'], '%H:%M').time()
    et = datetime.strptime(state['end'], '%H:%M').time()

    if not state.get('overlap_checked'):
        if check_overlap(turf, bd, st, et):
            alts = _suggest_alts(user, turf, bd, st, et)
            alt_en = " Try " + " or ".join(alts) + "." if alts else ""
            alt_hi = " " + " ya ".join(alts) + " try kariye." if alts else ""
            state.pop('start', None); state.pop('end', None)
            return {'reply': pick(R(
                f"{turf.name} is busy then.{alt_en} What other time?",
                f"{turf.name} us time pe booked hai.{alt_hi} Doosra time?"
            ), lang),
            'context': {'flow': 'booking', 'state': state}}
        state['overlap_checked'] = True

    # Step 4: phone
    if 'phone' not in state and latest_text:
        p = parse_phone(latest_text)
        if p: state['phone'] = p
    if 'phone' not in state:
        return {'reply': pick(R("Customer's 10-digit phone?",
                                 "Customer ka 10 digit phone?"), lang),
                'context': {'flow': 'booking', 'state': state}}

    # Returning customer auto-fill
    existing = Customer.objects.filter(owner=user, phone=state['phone']).first()
    if existing and 'name' not in state:
        state['name'] = existing.name
        state['returning'] = True

    # Step 5: name
    if 'name' not in state and latest_text:
        nm = re.sub(r'^(name is|naam hai|naam|the name)\s+', '', latest_text.strip(), flags=re.IGNORECASE)
        nm = nm.strip().title()
        if nm and len(nm) > 1 and not re.search(r'\d', nm):
            state['name'] = nm
    if 'name' not in state:
        return {'reply': pick(R("Customer name?", "Customer ka naam?"), lang),
                'context': {'flow': 'booking', 'state': state}}

    # Step 6: amount
    if 'amount' not in state:
        state['amount'] = int(calculate_price(turf, bd, st, et))

    # Create
    try:
        customer, _ = Customer.objects.get_or_create(
            owner=user, phone=state['phone'], defaults={'name': state['name']}
        )
        if customer.name != state['name']:
            customer.name = state['name']; customer.save()

        booking = Booking.objects.create(
            owner=user, turf=turf, customer=customer,
            booking_date=bd, start_time=st, end_time=et,
            total_amount=Decimal(state['amount']), status='booked',
        )
        customer.total_bookings += 1
        customer.last_visit = bd
        customer.save()

        BookingLog.objects.create(
            booking=booking, action='created',
            description=f"Booking via chat — {turf.name}, {bd}, {state['start']}–{state['end']}, ₹{state['amount']}"
        )

        date_str = bd.strftime('%d %b')
        ret = " (returning)" if state.get('returning') else ""
        time_pretty = f"{fmt_time(state['start'])}–{fmt_time(state['end'])}"
        return {'reply': pick(R(
            f"✓ Booked! {turf.name} on {date_str} from {time_pretty} for {customer.name}{ret}. Total ₹{state['amount']}. View it?",
            f"✓ Ho gaya! {turf.name} {date_str} ko {time_pretty}, {customer.name} ke liye, ₹{state['amount']}. Dekhoge?"
        ), lang),
        'context': {'flow': 'post_booking', 'booking_id': booking.id}}
    except Exception as e:
        return {'reply': pick(R(f"Booking failed: {e}", f"Booking nahi bani: {e}"), lang)}


# ════════════════════════════════════════════════════════
# CANCEL / HOLD / COMPLETE
# ════════════════════════════════════════════════════════

def parse_booking_id(text):
    m = re.search(r'\b(?:#|booking[\s#]*)?(\d+)\b', text)
    if m: return int(m.group(1))
    return None


def _change_status(user, text, new_status, lang):
    bid = parse_booking_id(text)
    if not bid:
        return {'reply': pick(R("Booking ID? Like 'cancel booking 12'.",
                                 "Booking ID batao, jaise 'booking 12 cancel'."), lang)}
    b = Booking.objects.filter(id=bid, owner=user).first()
    if not b:
        return {'reply': pick(R(f"No booking #{bid} found.", f"Booking #{bid} nahi mili."), lang)}
    if b.status == new_status:
        return {'reply': pick(R(f"Booking #{bid} is already {new_status}.",
                                 f"Booking #{bid} already {new_status} hai."), lang)}
    old = b.status
    b.status = new_status
    b.save()
    BookingLog.objects.create(
        booking=b, action='status_changed',
        description=f"Status: {old} → {new_status} (via chat)"
    )
    return {'reply': pick(R(
        f"Done. Booking #{bid} is now {new_status}.",
        f"Ho gaya. Booking #{bid} ab {new_status} hai."
    ), lang),
    'redirect': f'/bookings/{bid}/'}

def handle_update_booking_amount(user, text, lang):
    """Update an existing booking's total_amount or add advance payment."""
    bid = parse_booking_id(text)
    new_amount = parse_amount(text)

    if not bid:
        return {'reply': pick(R(
            "Booking ID? Like 'set booking 12 amount 2000'.",
            "Booking ID batao, jaise 'booking 12 ka amount 2000 karo'."
        ), lang)}

    if not new_amount:
        return {'reply': pick(R(
            "What amount? Say 'amount 2200'.",
            "Kitna amount? Bolo 'amount 2200'."
        ), lang)}

    b = Booking.objects.filter(id=bid, owner=user).first()
    if not b:
        return {'reply': pick(R(f"No booking #{bid}.", f"Booking #{bid} nahi mili."), lang)}

    old = b.total_amount
    b.total_amount = Decimal(new_amount)
    b.update_payment_status()
    b.save()
    BookingLog.objects.create(
        booking=b, action='amount_changed',
        description=f"Amount: ₹{old} → ₹{new_amount} (via chat)"
    )
    return {'reply': pick(R(
        f"Done. Booking #{bid} amount updated to ₹{new_amount}.",
        f"Ho gaya. Booking #{bid} ka amount ₹{new_amount} ho gaya."
    ), lang),
    'redirect': f'/bookings/{bid}/'}


def handle_add_payment(user, text, lang):
    """Add a payment (advance or full) to a booking."""
    from .models import Payment

    bid = parse_booking_id(text)
    amount = parse_amount(text)

    if not bid or not amount:
        return {'reply': pick(R(
            "Tell me booking ID and amount, like 'add 500 to booking 12'.",
            "Booking ID aur amount batao, jaise '500 add karo booking 12 me'."
        ), lang)}

    b = Booking.objects.filter(id=bid, owner=user).first()
    if not b:
        return {'reply': pick(R(f"No booking #{bid}.", f"Booking #{bid} nahi mili."), lang)}

    # Detect payment method
    method = 'cash'
    t = text.lower()
    if 'upi' in t: method = 'upi'
    elif 'card' in t: method = 'card'
    elif 'bank' in t: method = 'bank_transfer'

    Payment.objects.create(
        booking=b, amount=Decimal(amount), method=method
    )
    b.paid_amount = (b.paid_amount or Decimal('0')) + Decimal(amount)
    b.update_payment_status()
    b.save()
    BookingLog.objects.create(
        booking=b, action='payment_added',
        description=f"Added ₹{amount} via {method.upper()} (via chat)"
    )
    return {'reply': pick(R(
        f"Added ₹{amount} via {method.upper()} to booking #{bid}. Remaining: ₹{int(b.remaining_amount)}.",
        f"Booking #{bid} me ₹{amount} ({method.upper()}) add ho gaya. Baki: ₹{int(b.remaining_amount)}."
    ), lang),
    'redirect': f'/bookings/{bid}/'}


def handle_cancel(user, text, lang):
    return _change_status(user, text, 'cancelled', lang)


def handle_hold(user, text, lang):
    return _change_status(user, text, 'hold', lang)


def handle_complete(user, text, lang):
    return _change_status(user, text, 'completed', lang)


# ════════════════════════════════════════════════════════
# MAIN ENDPOINT
# ════════════════════════════════════════════════════════

@login_required
def chat_process(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
        msg = (data.get('message') or '').strip()
        context = data.get('context')
        if not msg:
            return JsonResponse({'success': False, 'error': 'Empty message'})

        lang = detect_lang(msg)
        user = request.user

        # ── MULTI-TURN FLOWS ──
        if context and context.get('flow') == 'booking':
            result = continue_booking_flow(user, context.get('state', {}), lang, latest_text=msg)
            return _respond(msg, 'create_booking', result)
        
        # ── AVAILABILITY multi-turn ──
        if context and context.get('flow') == 'availability':
            result = handle_check_availability(user, msg, lang, prefilled=context.get('state', {}))
            return _respond(msg, 'check_availability', result)

        if context and context.get('flow') == 'confirm_book':
            intent = detect_intent(msg)
            if intent == 'yes':
                pre = {'turf_id': context['turf_id'], 'date': context['date'],
                       'start': context['start'], 'end': context['end'],
                       'overlap_checked': True}
                result = continue_booking_flow(user, pre, lang)
                return _respond(msg, 'create_booking', result)
            if intent == 'no' or intent == 'close':
                return _respond(msg, 'cancel',
                    {'reply': pick(R("Okay, cancelled.", "Theek hai."), lang)})

        if context and context.get('flow') == 'post_booking':
            bid = context.get('booking_id')
            intent_local = detect_intent(msg)

            if intent_local == 'yes':
                return _respond(msg, 'redirect',
                    {'reply': pick(R("Opening booking...", "Booking khol raha..."), lang),
                     'redirect': f'/bookings/{bid}/', 'lang': lang})

            # ⭐ Detect amount / payment edits in natural language
            new_amount = parse_amount(msg)
            t = msg.lower()
            mentions_total = re.search(r'\b(total|amount|price)\b', t)
            mentions_advance = re.search(r'\b(advance|paid|payment|paisa)\b', t)
            method = 'cash'
            if 'upi' in t: method = 'upi'
            elif 'card' in t: method = 'card'
            elif 'bank' in t: method = 'bank_transfer'

            # User said both total and advance e.g. "total 2200 advance 500 upi"
            total_match = re.search(r'\btotal[^\d]*(\d+)', t)
            adv_match = re.search(r'(?:advance|paid|paisa)[^\d]*(\d+)', t)

            if total_match or adv_match:
                from .models import Payment
                b = Booking.objects.filter(id=bid, owner=user).first()
                if b:
                    msgs_en, msgs_hi = [], []
                    if total_match:
                        new_total = int(total_match.group(1))
                        old_total = int(b.total_amount)
                        b.total_amount = Decimal(new_total)
                        BookingLog.objects.create(
                            booking=b, action='amount_changed',
                            description=f"Amount: ₹{old_total} → ₹{new_total} (via chat)"
                        )
                        msgs_en.append(f"Total updated to ₹{new_total}")
                        msgs_hi.append(f"Total ₹{new_total} ho gaya")
                    if adv_match:
                        adv = int(adv_match.group(1))
                        Payment.objects.create(
                            booking=b, amount=Decimal(adv), method=method
                        )
                        b.paid_amount = (b.paid_amount or Decimal('0')) + Decimal(adv)
                        BookingLog.objects.create(
                            booking=b, action='payment_added',
                            description=f"Added ₹{adv} via {method.upper()} (via chat)"
                        )
                        msgs_en.append(f"₹{adv} added via {method.upper()}")
                        msgs_hi.append(f"₹{adv} ({method.upper()}) add ho gaya")
                    b.update_payment_status()
                    b.save()
                    return _respond(msg, 'updated', {
                        'reply': pick(R(
                            f"✓ {', '.join(msgs_en)}. Remaining: ₹{int(b.remaining_amount)}.",
                            f"✓ {', '.join(msgs_hi)}. Baki: ₹{int(b.remaining_amount)}."
                        ), lang),
                        'lang': lang,
                        'redirect': f'/bookings/{bid}/',
                    })

            if intent_local == 'no':
                return _respond(msg, 'continue',
                    {'reply': pick(R("Okay. Anything else?", "Theek. Aur kuch?"), lang), 'lang': lang})
            # Other intents — fall through to normal processing

        # ── NORMAL INTENT ──
        intent = detect_intent(msg)

        # 🧠 Self-learning layer — fuzzy match against past successful queries
        if intent == 'unknown':
            fuzzy = fuzzy_match_memory(user, msg)
            if fuzzy:
                intent = fuzzy

        # 🧠 Token-overlap fallback — match against known intent keyword sets
        if intent == 'unknown':
            tok = token_fallback_intent(msg)
            if tok:
                intent = tok

        h = {
            'greet':              lambda: handle_greet(user, lang),
            'close':              lambda: handle_close(lang),
            'thanks':             lambda: handle_thanks(lang),
            'help':               lambda: handle_help(lang),
            'create_booking':     lambda: start_booking_flow(user, msg, lang),
            'check_availability': lambda: handle_check_availability(user, msg, lang),
            'cancel_booking':     lambda: handle_cancel(user, msg, lang),
            'hold_booking':       lambda: handle_hold(user, msg, lang),
            'complete_booking':   lambda: handle_complete(user, msg, lang),
            'revenue_today':      lambda: handle_revenue_today(user, lang),
            'revenue_yesterday':  lambda: handle_revenue_yesterday(user, lang),
            'revenue_week':       lambda: handle_revenue_week(user, lang),
            'revenue_month':      lambda: handle_revenue_month(user, lang),
            'pending_today':      lambda: handle_pending_today(user, lang),
            'pending_week':       lambda: handle_pending_week(user, lang),
            'pending_month':      lambda: handle_pending_month(user, lang),
            'pending_all':        lambda: handle_pending_all(user, lang),
            'received_today':     lambda: handle_received_today(user, lang),
            'received_yesterday': lambda: handle_received_yesterday(user, lang),
            'received_week':      lambda: handle_received_week(user, lang),
            'received_month':     lambda: handle_received_month(user, lang),
            'bookings_today':     lambda: handle_bookings_today(user, lang),
            'top_customers':      lambda: handle_top_customers(user, lang),
            'total_customers':    lambda: handle_total_customers(user, lang),
            'total_turfs':        lambda: handle_total_turfs(user, lang),
            'show_customer_bookings': lambda: handle_show_customer_bookings(user, msg, lang),
            'update_booking_amount':  lambda: handle_update_booking_amount(user, msg, lang),
            'add_payment':            lambda: handle_add_payment(user, msg, lang),
            'faq_create_booking': lambda: faq_create_booking(lang),
            'faq_pricing':        lambda: faq_pricing(lang),
            'faq_add_turf':       lambda: faq_add_turf(lang),
            'faq_overnight':      lambda: faq_overnight(lang),
            'faq_edit_booking':   lambda: faq_edit_booking(lang),
        }
        result = h.get(intent, lambda: handle_unknown(msg, lang))()
        result.setdefault('lang', lang)

        # 🧠 Remember successful queries for future learning
        if intent != 'unknown':
            remember_query(user, msg, intent)

        return _respond(msg, intent, result)

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)})


# Smart suggestion chips per intent + language
SUGGESTIONS = {
    'en': {
        'greet':         ["Today's revenue", "Pending today", "How to create booking", "Top customers"],
        'revenue_today': ["This week revenue", "Today's pending", "Today's bookings", "Yesterday revenue"],
        'revenue_yesterday': ["Today's revenue", "This week revenue", "Pending today"],
        'revenue_week':  ["This month revenue", "Today's revenue", "Pending today"],
        'revenue_month': ["This week revenue", "Top customers", "Pending total"],
        'pending_today': ["Total pending", "Received today", "This week pending"],
        'pending_all':   ["Pending today", "Received today", "Top customers"],
        'pending_week':  ["Pending today", "Pending total", "Received this week"],
        'pending_month': ["Pending today", "Pending total", "Received this month"],
        'received_today': ["Today's revenue", "Pending today", "Today's bookings"],
        'received_week': ["This week revenue", "Pending total", "Top customers"],
        'received_month': ["This month revenue", "Top customers", "Pending total"],
        'received_yesterday': ["Today's revenue", "Pending today"],
        'bookings_today': ["Today's revenue", "Pending today", "Top customers"],
        'top_customers': ["Total customers", "Today's revenue", "Total turfs"],
        'create_booking': ["Today's bookings", "Today's revenue"],
        'check_availability': ["Today's revenue", "Pending today"],
        'cancel_booking': ["Today's bookings", "Pending today"],
        'hold_booking': ["Today's bookings"],
        'complete_booking': ["Today's revenue", "Today's bookings"],
        'help':          ["Today's revenue", "Pending today", "How to create booking"],
        'unknown':       ["Today's revenue", "Pending today", "How to create booking", "Help"],
        'thanks':        ["Today's revenue", "Pending today", "Top customers"],
        'faq_create_booking': ["Today's revenue", "How does pricing work"],
        'faq_pricing':   ["How to create booking", "Today's revenue"],
        'faq_add_turf':  ["How to create booking", "How does pricing work"],
        'faq_overnight': ["How does pricing work", "How to create booking"],
        'faq_edit_booking': ["How to create booking", "Today's bookings"],
        'show_customer_bookings': ["Today's revenue", "Pending today", "Top customers"],
        'update_booking_amount':  ["Today's bookings", "Pending today"],
        'add_payment':            ["Today's revenue", "Pending today"],
        'updated':                ["Today's bookings", "Today's revenue", "Pending today"],
    },
    'hi': {
        'show_customer_bookings': ["Aaj ka revenue", "Aaj ka pending", "Top customers"],
        'update_booking_amount':  ["Aaj ki bookings", "Aaj ka pending"],
        'add_payment':            ["Aaj ka revenue", "Aaj ka pending"],
        'updated':                ["Aaj ki bookings", "Aaj ka revenue", "Aaj ka pending"],
        'greet':         ["Aaj ka revenue", "Aaj kitne pending", "Booking kaise banao", "Top customers"],
        'revenue_today': ["Is hafte ki kamai", "Aaj ka pending", "Aaj ki bookings", "Kal ka revenue"],
        'revenue_yesterday': ["Aaj ka revenue", "Is hafte ki kamai", "Aaj ka pending"],
        'revenue_week':  ["Is mahine ki kamai", "Aaj ka revenue", "Aaj ka pending"],
        'revenue_month': ["Is hafte ki kamai", "Top customers", "Total pending"],
        'pending_today': ["Total pending", "Aaj kitna aaya", "Is hafte ka pending"],
        'pending_all':   ["Aaj ka pending", "Aaj kitna aaya", "Top customers"],
        'pending_week':  ["Aaj ka pending", "Total pending", "Is hafte aaya kitna"],
        'pending_month': ["Aaj ka pending", "Total pending", "Is mahine aaya kitna"],
        'received_today': ["Aaj ka revenue", "Aaj ka pending", "Aaj ki bookings"],
        'received_week': ["Is hafte ki kamai", "Total pending", "Top customers"],
        'received_month': ["Is mahine ki kamai", "Top customers", "Total pending"],
        'received_yesterday': ["Aaj ka revenue", "Aaj ka pending"],
        'bookings_today': ["Aaj ka revenue", "Aaj ka pending", "Top customers"],
        'top_customers': ["Total customers", "Aaj ka revenue", "Total turfs"],
        'create_booking': ["Aaj ki bookings", "Aaj ka revenue"],
        'check_availability': ["Aaj ka revenue", "Aaj ka pending"],
        'cancel_booking': ["Aaj ki bookings", "Aaj ka pending"],
        'hold_booking': ["Aaj ki bookings"],
        'complete_booking': ["Aaj ka revenue", "Aaj ki bookings"],
        'help':          ["Aaj ka revenue", "Aaj ka pending", "Booking kaise banao"],
        'unknown':       ["Aaj ka revenue", "Aaj ka pending", "Booking kaise banao", "Help"],
        'thanks':        ["Aaj ka revenue", "Aaj ka pending", "Top customers"],
        'faq_create_booking': ["Aaj ka revenue", "Pricing kaise kaam karta"],
        'faq_pricing':   ["Booking kaise banao", "Aaj ka revenue"],
        'faq_add_turf':  ["Booking kaise banao", "Pricing kaise kaam karta"],
        'faq_overnight': ["Pricing kaise kaam karta", "Booking kaise banao"],
        'faq_edit_booking': ["Booking kaise banao", "Aaj ki bookings"],
    }
}


def _respond(msg, intent, result):
    lang = result.get('lang', 'en')
    chips = SUGGESTIONS.get(lang, SUGGESTIONS['en']).get(intent, SUGGESTIONS[lang]['unknown'])
    return JsonResponse({
        'success': True,
        'message': msg,
        'intent': intent,
        'reply': result.get('reply', ''),
        'lang': lang,
        'redirect': result.get('redirect'),
        'close': result.get('close', False),
        'context': result.get('context'),
        'suggestions': chips,
    })


def handle_received_today(user, lang):
    today = timezone.localtime().date()
    c, r = _rev_in(user, today, today)
    if r == 0:
        return {'reply': pick(R("Today: no payment received yet.", "Aaj abhi koi payment nahi aaya."), lang)}
    return {'reply': pick(R(f"Today: ₹{r} received from {c} bookings.",
                            f"Aaj: ₹{r} mil gaya, {c} bookings se."), lang)}


def handle_received_yesterday(user, lang):
    today = timezone.localtime().date()
    y = today - timedelta(days=1)
    c, r = _rev_in(user, y, y)
    return {'reply': pick(R(f"Yesterday: ₹{r} received from {c} bookings.",
                            f"Kal: ₹{r} mila tha, {c} bookings se."), lang)}


def handle_received_week(user, lang):
    today = timezone.localtime().date()
    ws = today - timedelta(days=today.weekday())
    c, r = _rev_in(user, ws, today)
    return {'reply': pick(R(f"This week: ₹{r} received from {c} bookings.",
                            f"Is hafte: ₹{r} mila, {c} bookings se."), lang)}


def handle_received_month(user, lang):
    today = timezone.localtime().date()
    ms = today.replace(day=1)
    c, r = _rev_in(user, ms, today)
    return {'reply': pick(R(f"This month: ₹{r} received from {c} bookings.",
                            f"Is mahine: ₹{r} mila, {c} bookings se."), lang)}