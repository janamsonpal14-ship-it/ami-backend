from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from app.config import IST

_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = [
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety",
]


def ist_now() -> datetime:
    """Return current timezone-aware datetime in Asia/Kolkata."""
    return datetime.now(IST)


def ist_today() -> date:
    """Return today's date in Asia/Kolkata."""
    return ist_now().date()


def format_indian_date(value: date | None) -> str:
    """Format date as '12 Nov 2026'."""
    if value is None:
        return ""
    return value.strftime("%d %b %Y")


def _two_digits_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    ten, one = divmod(n, 10)
    return f"{_TENS[ten]} {_ONES[one]}".strip()


def _three_digits_words(n: int) -> str:
    hundred, rem = divmod(n, 100)
    parts: list[str] = []
    if hundred:
        parts.append(f"{_ONES[hundred]} Hundred")
    if rem:
        parts.append(_two_digits_words(rem))
    return " ".join(parts)


def integer_to_indian_words(num: int) -> str:
    """Convert a non-negative integer into Indian-system words (Lakhs / Crores)."""
    if num == 0:
        return "Zero"
    parts: list[str] = []
    crore, rem = divmod(num, 10_000_000)
    if crore:
        parts.append(f"{integer_to_indian_words(crore)} Crore")
    lakh, rem = divmod(rem, 100_000)
    if lakh:
        parts.append(f"{_two_digits_words(lakh)} Lakh")
    thousand, rem = divmod(rem, 1_000)
    if thousand:
        parts.append(f"{_two_digits_words(thousand)} Thousand")
    if rem:
        parts.append(_three_digits_words(rem))
    return " ".join(parts)


def amount_to_indian_words(amount: Decimal | float | int) -> str:
    """Format a Rupee amount into formal Indian legal memo words."""
    dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = dec < 0
    dec = abs(dec)
    rupees = int(dec)
    paise = int((dec - rupees) * 100)
    words = f"Rupees {integer_to_indian_words(rupees)}"
    if paise:
        words += f" and Paise {_two_digits_words(paise)}"
    words += " Only"
    return f"Minus {words}" if negative else words


def format_indian_currency(amount: Decimal | float | int) -> str:
    """Format number with Indian digit grouping (e.g. ₹1,20,000)."""
    dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if dec < 0 else ""
    dec = abs(dec)
    whole = int(dec)
    paise = int((dec - whole) * 100)

    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        last_three = s[-3:]
        rest = s[:-3]
        chunks: list[str] = []
        while len(rest) > 2:
            chunks.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            chunks.append(rest)
        grouped = ",".join(reversed(chunks)) + "," + last_three

    if paise:
        return f"{sign}₹{grouped}.{paise:02d}"
    return f"{sign}₹{grouped}"

