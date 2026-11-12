"""Readable prices for Wiro AI models and tasks.

A model's price comes from its ``dynamicprice`` list when it has one: rows of ``price`` and
``priceMethod``, each for the settings in its ``inputs``. Without a list, a run is billed for its
run time (``cps``, else ``quegroupcps``) and ``approximatelycost`` is a typical run. A finished
task carries ``totalcost`` (list price) and, once billed, ``chargedcost``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

PRICE_METHOD_LABELS = {
    "cpr": "per run",
    "cps": "per second of run time",
    "cpo": "per output",
    "cpt": "per input and output token",
    "cp-pixel": "by image size",
    "cp-audiosecondslength": "per second of input audio",
    "cp-promptlength": "per prompt character",
    "cp-outputVideoLength": "per video second",
    "cp-realtimeturn": "per turn",
    "cp-readoutput": "set by the run when it ends",
}
NO_FIXED_PRICE = "no fixed price: the charge follows the run time and is set when the run ends"
_QUANTITY = re.compile(r"^QUANTITY:(\d+)$")


def money(value: Any) -> str | None:
    """$0.0336 style text for an amount, None when it is not a number."""
    amount = number(value)
    if amount is None:
        return None
    text = format(Decimal(str(round(amount, 6))).normalize(), "f")
    return f"${text}"


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        amount = float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount == amount else None  # NaN


def price_rows(model: Mapping[str, Any]) -> list[dict]:
    raw = model.get("dynamicprice")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            return []
    return [row for row in raw or [] if isinstance(row, dict)] if isinstance(raw, list) else []


def _note(row: Mapping[str, Any]) -> str:
    return " ".join(str(row.get("note") or "").split())


def _run_time_rate(model: Mapping[str, Any]) -> float:
    own = number(model.get("cps")) or 0.0
    return own if own > 0 else (number(model.get("quegroupcps")) or 0.0)


def price_summary(model: Mapping[str, Any]) -> str:
    """The model's price on one line."""
    rows = price_rows(model)
    if rows:
        if any(row.get("priceMethod") == "cp-realtimeturn" for row in rows):
            note = next((_note(row) for row in rows if _note(row)), "")
            return f"billed turn by turn in a live session{': ' + note if note else ''}"
        billed = [row for row in rows if row.get("priceMethod") in PRICE_METHOD_LABELS]
        if not billed:
            return NO_FIXED_PRICE
        parts: list[str] = []
        unit_rows = [row for row in billed if row.get("priceMethod") not in ("cp-readoutput", "cp-pixel")]
        if unit_rows:
            by_label: dict[str, list[float]] = {}
            for row in unit_rows:
                amount = number(row.get("price"))
                if amount is not None:
                    by_label.setdefault(PRICE_METHOD_LABELS[str(row["priceMethod"])], []).append(amount)
            for label, amounts in by_label.items():
                low, high = min(amounts), max(amounts)
                span = money(low) if low == high else f"{money(low)} to {money(high)}"
                parts.append(f"{span} {label}")
        pixel = [row for row in billed if row.get("priceMethod") == "cp-pixel"]
        if pixel:
            amounts = [number(row.get("price")) or 0 for row in pixel]
            parts.append(f"from {money(min(amounts))} per image, by image size")
        for row in billed:
            if row.get("priceMethod") == "cp-readoutput":
                estimate = number(row.get("price")) or 0
                note = _note(row)
                text = "set by the run when it ends"
                if estimate > 0:
                    text += f" (estimate {money(estimate)})"
                if note:
                    text += f": {note}"
                if text not in parts:
                    parts.append(text)
        line = "; ".join(parts) or NO_FIXED_PRICE
        if len(billed) < len(rows):
            line += "; other settings have no fixed price"
        return line
    approx = number(model.get("approximatelycost")) or 0
    if approx > 0:
        return f"about {money(approx)} per run (billed for run time)"
    rate = _run_time_rate(model)
    if rate > 0:
        return f"{money(rate)} per second of run time"
    return "billed for run time"


def price_table(model: Mapping[str, Any], limit: int = 40) -> list[dict]:
    """The price rows as wiro_model_info shows them: settings, price and unit."""
    table = []
    for row in price_rows(model)[:limit]:
        method = str(row.get("priceMethod") or "")
        entry: dict[str, Any] = {
            "settings": dict(row.get("inputs") or {}),
            "price_usd": number(row.get("price")),
            "unit": PRICE_METHOD_LABELS.get(method, "no fixed price"),
        }
        for extra in ("priceExtra", "priceInput"):
            if number(row.get(extra)):
                entry["extra_block_usd" if extra == "priceExtra" else "per_input_block_usd"] = number(row.get(extra))
        if _note(row):
            entry["note"] = _note(row)[:240]
        table.append(entry)
    return table


def _same(row_value: Any, value: Any) -> bool:
    if isinstance(row_value, (int, float)) and not isinstance(row_value, bool):
        try:
            return float(value) == float(row_value)
        except (TypeError, ValueError):
            return False
    return str(row_value).strip().lower() == str(value).strip().lower()


def _count_of(value: Any) -> int:
    if isinstance(value, list):
        return len([v for v in value if v not in (None, "")])
    if isinstance(value, str) and value.strip():
        return len([v for v in value.split(",") if v.strip()])
    return 0


def expected_price(model: Mapping[str, Any], params: Mapping[str, Any], outputs: int = 1) -> float | None:
    """The list price of one run with these settings, when the price list fixes it (per run or
    per output rows); None when it depends on the run (time, tokens, length, size)."""
    rows = [row for row in price_rows(model) if row.get("priceMethod") in ("cpr", "cpo")]
    if not rows:
        return None
    # A run uses the row whose settings all match; among QUANTITY rows, the highest count at or
    # below the number of files sent.
    best: tuple[int, int, float, str] | None = None
    for row in rows:
        inputs = row.get("inputs") or {}
        matched = quantities = 0
        ok = True
        for key, row_value in inputs.items():
            quantity = _QUANTITY.match(str(row_value))
            if quantity:
                if _count_of(params.get(key)) < int(quantity.group(1)):
                    ok = False
                    break
                matched += 1
                quantities += int(quantity.group(1))
                continue
            if key not in params or not _same(row_value, params[key]):
                ok = False
                break
            matched += 1
        price = number(row.get("price"))
        if ok and price is not None and (best is None or (matched, quantities) > best[:2]):
            best = (matched, quantities, price, str(row.get("priceMethod")))
    if best is None:
        return None
    _matched, _quantities, price, method = best
    return round(price * max(1, outputs), 6) if method == "cpo" else price


def task_cost(task: Mapping[str, Any]) -> tuple[float | None, str]:
    """(USD amount, text) for a task: the charge once Wiro AI has billed it, else the list price."""
    listed = number(task.get("totalcost"))
    charged = number(task.get("chargedcost"))
    status = task.get("status")
    if status == "task_cancel":
        accepted = task.get("accepttime")
        if charged:
            return charged, f"{money(charged)} charged"
        if accepted not in (None, "") and number(accepted) == 0:
            return 0.0, "$0 (cancelled while queued, not charged)"
        return None, "not known yet: a task stopped while running can still be charged when its work ends"
    if charged is not None:
        if charged == 0:
            return 0.0, "$0 (not charged)"
        if listed and abs(listed - charged) > 1e-9:
            return charged, f"{money(charged)} charged (list price {money(listed)})"
        return charged, f"{money(charged)} charged"
    if listed is not None and listed > 0:
        return listed, f"{money(listed)} list price; the charged amount is recorded in a moment"
    return listed, money(listed) or "not reported"
