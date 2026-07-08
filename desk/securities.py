"""Load and look up securities from data/securities.csv.

CSV columns: sec_id,symbol,name,asset_type,market,price_source

Note (Phase 2): TASE stock prices from yfinance are quoted in ILA (agorot);
divide by 100 to get ILS. Not handled here — this module is lookup-only.
"""
import csv
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "securities.csv"


@dataclass(frozen=True)
class Security:
    sec_id: str
    symbol: str
    name: str
    asset_type: str
    market: str
    price_source: str


def load_securities(csv_path: str | Path | None = None) -> list[Security]:
    path = Path(csv_path) if csv_path else DEFAULT_CSV_PATH
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            Security(
                sec_id=row["sec_id"],
                symbol=row["symbol"],
                name=row["name"],
                asset_type=row["asset_type"],
                market=row["market"],
                price_source=row["price_source"],
            )
            for row in reader
        ]


def find(query: str, securities: list[Security] | None = None) -> Security | None:
    """Match query against sec_id, symbol (exact, case-insensitive), or name (substring)."""
    securities = securities if securities is not None else load_securities()
    q = query.strip().lower()
    if not q:
        return None
    for s in securities:
        if s.sec_id.lower() == q or s.symbol.lower() == q:
            return s
    for s in securities:
        if q in s.name.lower():
            return s
    return None


if __name__ == "__main__":
    secs = load_securities()
    print(f"loaded {len(secs)} securities")
    for query in ["AAPL", "629014", "teva", "nope"]:
        print(query, "->", find(query, secs))
