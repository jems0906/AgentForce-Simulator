from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import httpx

from agentforce_simulator.storage.base import StorageBackend


@dataclass(slots=True)
class WeatherTool:
    async def run(self, city: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            geo.raise_for_status()
            geo_payload = geo.json()
            results = geo_payload.get("results") or []
            if not results:
                raise ValueError(f"Could not find weather data for {city}.")
            target = results[0]
            weather = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": target["latitude"],
                    "longitude": target["longitude"],
                    "current": "temperature_2m,wind_speed_10m,weather_code",
                },
            )
            weather.raise_for_status()
            current = weather.json().get("current", {})
            return {
                "city": target["name"],
                "country": target.get("country"),
                "temperature_c": current.get("temperature_2m"),
                "wind_speed_kph": current.get("wind_speed_10m"),
                "weather_code": current.get("weather_code"),
            }


@dataclass(slots=True)
class StockTool:
    async def run(self, symbol: str) -> dict[str, Any]:
        ticker = symbol.upper().strip()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}")
            response.raise_for_status()
            payload = response.json()
            result = payload.get("chart", {}).get("result")
            if not result:
                raise ValueError(f"Could not fetch price data for {ticker}.")
            meta = result[0].get("meta", {})
            timestamps = result[0].get("timestamp") or []
            closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close") or []
            series = [
                {"timestamp": ts, "close": close}
                for ts, close in zip(timestamps[-10:], closes[-10:])
                if close is not None
            ]
            return {
                "symbol": ticker,
                "currency": meta.get("currency", "USD"),
                "regular_market_price": meta.get("regularMarketPrice"),
                "previous_close": meta.get("previousClose"),
                "series": series,
            }


@dataclass(slots=True)
class DatabaseTool:
    storage: StorageBackend

    async def run(self, query: str) -> dict[str, Any]:
        rows = await self.storage.run_sql(query)
        return {"query": query, "row_count": len(rows), "rows": rows}


@dataclass(slots=True)
class SupportCaseTool:
    storage: StorageBackend

    async def run(self, case_id: int) -> dict[str, Any]:
        query = (
            "select id, category, status, priority, satisfaction, region "
            f"from support_cases where id = {case_id}"
        )
        rows = await self.storage.run_sql(query)
        if not rows:
            raise ValueError(f"Could not find support case {case_id}.")
        return {"case_id": case_id, "case": rows[0], "query": query}


@dataclass(slots=True)
class OperationsSummaryTool:
    storage: StorageBackend

    async def run(self) -> dict[str, Any]:
        query = (
            "select "
            "count(*) as total_cases, "
            "sum(case when status = 'open' then 1 else 0 end) as open_cases, "
            "sum(case when priority = 'high' then 1 else 0 end) as high_priority_cases, "
            "round(cast(avg(satisfaction) as numeric), 2) as avg_satisfaction "
            "from support_cases"
        )
        rows = await self.storage.run_sql(query)
        return {"query": query, "summary": rows[0] if rows else {}}


@dataclass(slots=True)
class ToolCatalog:
    storage: StorageBackend
    weather: WeatherTool = field(init=False)
    stocks: StockTool = field(init=False)
    database: DatabaseTool = field(init=False)
    support_case: SupportCaseTool = field(init=False)
    operations_summary: OperationsSummaryTool = field(init=False)

    def __post_init__(self) -> None:
        self.weather = WeatherTool()
        self.stocks = StockTool()
        self.database = DatabaseTool(self.storage)
        self.support_case = SupportCaseTool(self.storage)
        self.operations_summary = OperationsSummaryTool(self.storage)

    @staticmethod
    def extract_city(message: str) -> str | None:
        match = re.search(r"weather\s+(?:in|for)\s+([A-Za-z\s]+)", message, re.IGNORECASE)
        return match.group(1).strip() if match else None

    @staticmethod
    def extract_stock_symbol(message: str) -> str | None:
        match = re.search(r"(?:stock|price|quote)\s+(?:for\s+)?([A-Za-z\.]{1,8})", message, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        cashtag = re.search(r"\$([A-Za-z\.]{1,8})", message)
        return cashtag.group(1).upper() if cashtag else None

    @staticmethod
    def extract_case_id(message: str) -> int | None:
        match = re.search(r"(?:case|ticket)\s+#?(\d+)", message, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def needs_operations_summary(message: str) -> bool:
        lowered = message.lower()
        return any(token in lowered for token in {"operations summary", "ops summary", "overall summary", "health summary"})
