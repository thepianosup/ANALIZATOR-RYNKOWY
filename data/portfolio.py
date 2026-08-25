"""data/portfolio.py — Portfolio holdings persistence (JSON)."""
import json
from pathlib import Path
from datetime import date
from uuid import uuid4

PORTFOLIO_FILE = Path.home() / ".analizator_rynkowy" / "portfolio.json"
CLOSED_PORTFOLIO_FILE = Path.home() / ".analizator_rynkowy" / "portfolio_closed.json"


def _safe_float(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _new_holding_id() -> str:
    return uuid4().hex


def _normalize_holding_id(value) -> str:
    return str(value or "").strip()


def _ensure_holding_ids(items: list[dict]) -> tuple[list[dict], bool]:
    normalized: list[dict] = []
    seen_ids: set[str] = set()
    changed = False

    for entry in items:
        if not isinstance(entry, dict):
            changed = True
            continue

        item = dict(entry)
        holding_id = _normalize_holding_id(item.get("holding_id"))
        if not holding_id or holding_id in seen_ids:
            holding_id = _new_holding_id()
            item["holding_id"] = holding_id
            changed = True
        seen_ids.add(holding_id)
        normalized.append(item)

    return normalized, changed


def _resolve_close_metrics(position: dict, exit_price: float | None = None, realized_pnl_abs: float | None = None) -> dict:
    shares = _safe_float(position.get("shares")) or 0.0
    avg_price = _safe_float(position.get("avg_price")) or 0.0
    resolved_exit_price = _safe_float(exit_price)
    resolved_pnl_abs = _safe_float(realized_pnl_abs)

    if resolved_exit_price is None and resolved_pnl_abs is None:
        return {
            "exit_price": None,
            "realized_pnl_abs": None,
            "realized_pnl_pct": None,
        }

    if resolved_pnl_abs is None and resolved_exit_price is not None:
        resolved_pnl_abs = (resolved_exit_price - avg_price) * shares
    elif resolved_exit_price is None and resolved_pnl_abs is not None and shares > 0:
        resolved_exit_price = avg_price + (resolved_pnl_abs / shares)

    resolved_pnl_pct = None
    if resolved_exit_price is not None and avg_price > 0:
        resolved_pnl_pct = ((resolved_exit_price - avg_price) / avg_price) * 100.0
    elif resolved_pnl_abs is not None and avg_price > 0 and shares > 0:
        resolved_pnl_pct = (resolved_pnl_abs / (avg_price * shares)) * 100.0

    return {
        "exit_price": round(resolved_exit_price, 4) if resolved_exit_price is not None else None,
        "realized_pnl_abs": round(resolved_pnl_abs, 2) if resolved_pnl_abs is not None else None,
        "realized_pnl_pct": round(resolved_pnl_pct, 2) if resolved_pnl_pct is not None else None,
    }


def load_portfolio() -> list[dict]:
    try:
        if PORTFOLIO_FILE.exists():
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    return []
                normalized, changed = _ensure_holding_ids(data)
                if changed:
                    save_portfolio(normalized)
                return normalized
    except Exception:
        pass
    return []


def save_portfolio(holdings: list[dict]):
    PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(holdings, f, indent=2, ensure_ascii=False)


def load_closed_positions() -> list[dict]:
    try:
        if CLOSED_PORTFOLIO_FILE.exists():
            with open(CLOSED_PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def save_closed_positions(positions: list[dict]):
    CLOSED_PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CLOSED_PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2, ensure_ascii=False)


def add_holding(
    ticker: str,
    shares: float,
    avg_price: float,
    notes: str = "",
    purchase_date: str = "",
    holding_id: str = "",
) -> list[dict]:
    holdings = load_portfolio()
    ticker = ticker.upper().strip()
    today = str(date.today())
    normalized_holding_id = _normalize_holding_id(holding_id)
    for h in holdings:
        if normalized_holding_id and _normalize_holding_id(h.get("holding_id")) == normalized_holding_id:
            h["ticker"] = ticker
            h["shares"] = shares
            h["avg_price"] = avg_price
            h["notes"] = notes
            if purchase_date:
                h["purchase_date"] = purchase_date
            h["holding_id"] = normalized_holding_id
            save_portfolio(holdings)
            return holdings
    holdings.append({
        "holding_id": normalized_holding_id or _new_holding_id(),
        "ticker": ticker,
        "shares": shares,
        "avg_price": avg_price,
        "notes": notes,
        "purchase_date": purchase_date or today,
        "added": today,
    })
    save_portfolio(holdings)
    return holdings


def remove_holding(ticker: str = "", holding_id: str = "") -> list[dict]:
    normalized_holding_id = _normalize_holding_id(holding_id)
    ticker = ticker.upper().strip()
    if normalized_holding_id:
        holdings = [h for h in load_portfolio() if _normalize_holding_id(h.get("holding_id")) != normalized_holding_id]
    else:
        holdings = [h for h in load_portfolio() if h["ticker"] != ticker]
    save_portfolio(holdings)
    return holdings


def close_holding(
    ticker: str = "",
    *,
    holding_id: str = "",
    exit_price: float | None = None,
    closed_date: str = "",
    realized_pnl_abs: float | None = None,
) -> list[dict]:
    holdings = load_portfolio()
    closed_positions = load_closed_positions()
    normalized_holding_id = _normalize_holding_id(holding_id)
    ticker = ticker.upper().strip()

    closed_index = next(
        (
            index
            for index, holding in enumerate(holdings)
            if (
                normalized_holding_id
                and _normalize_holding_id(holding.get("holding_id")) == normalized_holding_id
            )
            or (not normalized_holding_id and holding["ticker"] == ticker)
        ),
        None,
    )
    if closed_index is None:
        return holdings

    closed_item = holdings[closed_index]

    close_day = closed_date or str(date.today())
    archived = dict(closed_item)
    archived["status"] = "closed"
    archived["closed_date"] = close_day
    archived.update(_resolve_close_metrics(
        archived,
        exit_price=exit_price,
        realized_pnl_abs=realized_pnl_abs,
    ))

    closed_positions.append(archived)
    save_closed_positions(closed_positions)

    holdings.pop(closed_index)
    save_portfolio(holdings)
    return holdings


def update_closed_position(
    index: int,
    *,
    closed_date: str = "",
    exit_price: float | None = None,
    realized_pnl_abs: float | None = None,
) -> list[dict]:
    closed_positions = load_closed_positions()
    if index < 0 or index >= len(closed_positions):
        return closed_positions

    updated = dict(closed_positions[index])
    updated["status"] = "closed"
    updated["closed_date"] = closed_date or str(updated.get("closed_date") or date.today())
    updated.update(_resolve_close_metrics(
        updated,
        exit_price=exit_price,
        realized_pnl_abs=realized_pnl_abs,
    ))

    closed_positions[index] = updated
    save_closed_positions(closed_positions)
    return closed_positions
