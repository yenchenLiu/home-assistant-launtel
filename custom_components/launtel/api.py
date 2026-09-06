from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from aiohttp import ClientSession
from yarl import URL
from bs4 import BeautifulSoup
import re

__all__ = ["LauntelClient", "LauntelService"]

BASE_URL = URL("https://residential.launtel.net.au")


def _echarts_series(html: str, name: str) -> list[Optional[float]]:
    """Extract an ECharts series' numeric ``data`` array by its ``name:'...'``.

    The /usage page renders the monthly usage graph as an inline ECharts option
    object rather than exposing a separate JSON endpoint. Each daily bar/line is
    a quoted string (e.g. '87.51'); the 30-day-average line contains 'null'
    entries which are returned as ``None``.
    """
    i = html.find(f"name:'{name}'")
    if i == -1:
        return []
    start = html.find("[", html.find("data:", i))
    end = html.find("]", start)
    if start == -1 or end == -1:
        return []
    out: list[Optional[float]] = []
    for tok in re.findall(r"'([^']*)'", html[start:end + 1]):
        try:
            out.append(float(tok.strip()))
        except ValueError:
            out.append(None)
    return out


@dataclass
class LauntelService:
    title: str
    service_id: int
    avcid: str
    user_id: str
    speed_label: Optional[str] = field(default=None)  # e.g. "Fibre 250/100 Mbps" or "Fibre Home Ultrafast"
    change_in_progress: bool = field(default=False)


class LauntelClient:
    """Async client to interact with Launtel residential portal."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._logged_in = False
        self._lock = asyncio.Lock()

    async def async_login(self) -> None:
        async with self._lock:
            if self._logged_in:
                return
            resp = await self._session.post(
                BASE_URL / "login",
                data={"username": self._username, "password": self._password},
                allow_redirects=True,
            )
            text = await resp.text()
            if resp.status >= 400 or "name=\"username\"" in text:
                raise RuntimeError("Authentication failed with Launtel")
            self._logged_in = True

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.async_login()

    async def async_get_services(self) -> list[LauntelService]:
        await self._ensure_login()
        resp = await self._session.get(BASE_URL / "services")
        resp.raise_for_status()
        html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        service_cards = soup.find_all("div", class_="service-card")
        services: list[LauntelService] = []
        for card in service_cards:
            title_tag = card.find("span", class_="service-title-txt")
            if not title_tag:
                continue
            serv_title = title_tag.text.strip()
            chart = card.find("i", class_="fa-bar-chart")
            if not chart or not chart.parent or not chart.parent.get("href"):
                continue
            href = chart.parent.get("href")
            parts = href.split("=")
            serv_user_id = parts[2] if len(parts) > 2 else ""
            serv_avc_id = card.get("id", "")

            # Extract service_id from onclick handler (pauseService/unpauseService)
            pause_button = card.find("button", onclick=re.compile(r"(un)?pauseService\((\d+)"))
            serv_id: Optional[int] = None
            if pause_button and pause_button.has_attr("onclick"):
                m = re.search(r"(un)?pauseService\((\d+)", pause_button["onclick"])
                if m:
                    serv_id = int(m.group(2))

            # Extract Technology / Speed Tier -> full label
            speed_label: Optional[str] = None
            dt_speed = card.find("dt", string=re.compile(r"Technology\s*/\s*Speed\s*Tier", re.I))
            if dt_speed:
                dd = dt_speed.find_next("dd")
                if dd:
                    speed_label = " ".join(s.strip() for s in dd.stripped_strings)

            # Extract Status -> detect "Change in progress"
            change_in_progress = False
            dt_status = card.find("dt", string=re.compile(r"Status", re.I))
            if dt_status:
                dd_status = dt_status.find_next("dd")
                if dd_status and "Change in progress" in dd_status.get_text():
                    change_in_progress = True

            if serv_title and serv_id is not None and serv_avc_id and serv_user_id:
                services.append(
                    LauntelService(
                        title=serv_title,
                        service_id=serv_id,
                        avcid=serv_avc_id,
                        user_id=serv_user_id,
                        speed_label=speed_label,
                        change_in_progress=change_in_progress,
                    )
                )
        return services

    async def async_get_plan_options(self, avcid: str) -> tuple[list[str], dict[str, int], Optional[str], Optional[str], dict[int, dict[str, object]]]:
        """Return options, label_to_psid, current_label, locid, and a detailed plans mapping.

        plans mapping: { psid: {"label": str, "price_per_day": float, "unlimited": bool, "speed": Optional[str], "first_col": Optional[str]} }
        """
        await self._ensure_login()
        resp = await self._session.get(BASE_URL / "service", params={"avcid": avcid})
        resp.raise_for_status()
        html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")

        options: list[str] = []
        label_to_psid: dict[str, int] = {}
        current_label: Optional[str] = None
        plans_mapping: dict[int, dict[str, object]] = {}

        # Try to extract the current psid from hidden inputs or attributes
        current_psid: Optional[int] = None
        for selector in [
            "input[name='psid']",
            "input[name='current_psid']",
            "[data-current-psid]",
        ]:
            el = soup.select_one(selector)
            if el:
                val = el.get("value") or el.get("data-current-psid")
                if val:
                    try:
                        current_psid = int(val)
                        break
                    except ValueError:
                        pass

        speed_choices = soup.find_all("span", class_="list-group-item")
        for choice in speed_choices:
            # Extract PSID and price per day from attributes
            psid_str = choice.get("data-value")
            if isinstance(psid_str, (list, tuple)):
                psid_str = psid_str[0] if psid_str else None
            if not psid_str:
                continue
            psid = int(psid_str)
            plancharge_str = choice.get("data-plancharge")
            price_per_day: Optional[float] = None
            try:
                if plancharge_str is not None:
                    price_per_day = float(plancharge_str)
            except ValueError:
                price_per_day = None

            # Label and first column text
            first_col = None
            row = choice.find("div", class_="row")
            if row:
                cols = row.find_all("div", class_=re.compile(r"^col-"))
                if cols:
                    first_col = cols[0]
            target = first_col or choice
            first_col_text = " ".join(list(s.strip() for s in (first_col.stripped_strings if first_col else []))) if first_col else None
            label = " ".join(list(s.strip() for s in target.stripped_strings))
            label = re.sub(r"\s+", " ", label)

            # Parse speed from label parentheses, e.g., (250/100)
            m_speed = re.search(r"\((\d+)\s*/\s*(\d+)\)", label)
            plan_speed: Optional[str] = None
            if m_speed:
                plan_speed = f"{m_speed.group(1)}/{m_speed.group(2)}"

            # Unlimited
            unlimited = "Unlimited" in choice.get_text()

            if label:
                label_to_psid[label] = psid
                options.append(label)

            plans_mapping[psid] = {
                "label": label,
                "price_per_day": price_per_day,
                "unlimited": unlimited,
                "speed": plan_speed,
                "first_col": first_col_text,
            }

        # Compute current_label from current_psid if available
        if current_psid is not None:
            # Invert mapping to psid->label
            for label, pid in label_to_psid.items():
                if pid == current_psid:
                    current_label = label
                    break

        locid_input = soup.find("input", {"name": "locid"})
        locid = locid_input.get("value") if locid_input else None

        return options, label_to_psid, current_label, locid, plans_mapping

    async def async_change_plan(
        self,
        user_id: str,
        psid: int,
        service_id: int,
        avcid: str,
        locid: str,
        unpause: int = 0,
        *,
        scheduleddt: str = "",
        coat: str = "0",
        new_service_payment_option: str = "",
    ) -> None:
        """Apply a plan change following the portal flow using session cookies.

        1) GET confirm_service with full query to establish any cookies/server state.
        2) POST form-encoded data to confirm_service?userid=...
        """
        await self._ensure_login()

        confirm_get_url = (BASE_URL / "confirm_service").with_query(
            {
                "userid": str(user_id),
                "psid": str(psid),
                "unpause": str(unpause),
                "service_id": str(service_id),
                "upgrade_options": "",
                "discount_code": "",
                "avcid": avcid,
                "locid": locid,
                "coat": coat,
            }
        )
        get_resp = await self._session.get(confirm_get_url)
        get_resp.raise_for_status()
        await get_resp.text()

        form_data = {
            "userid": str(user_id),
            "psid": str(psid),
            "locid": locid,
            "avcid": avcid,
            "unpause": str(unpause),
            "scheduleddt": scheduleddt,
            "coat": coat,
            "new_service_payment_option": new_service_payment_option,
        }

        post_url = (BASE_URL / "confirm_service").with_query({"userid": str(user_id)})
        resp = await self._session.post(post_url, data=form_data)
        resp.raise_for_status()

    async def async_get_balance(self) -> Optional[float]:
        """Get the current account balance from the services page."""
        await self._ensure_login()
        resp = await self._session.get(BASE_URL / "services")
        resp.raise_for_status()
        html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")

        balance = None

        # Look for the specific current balance structure
        # Target: <dt>Current Balance</dt><dd><span>+$112.65</span></dd>
        # Note: dt may contain nested HTML elements, so we need to search by text content
        all_dts = soup.find_all("dt")
        balance_dt = None
        for dt in all_dts:
            if re.search(r"Current\s+Balance", dt.get_text(), re.I):
                balance_dt = dt
                break

        if balance_dt:
            dd_balance = balance_dt.find_next("dd")
            if dd_balance:
                # Look for the balance span within the dd element
                balance_span = dd_balance.find("span")
                if balance_span:
                    balance_text = balance_span.get_text(strip=True)

                    # Extract numeric value from text like '+$112.65' or '-$50.00'
                    balance_match = re.search(r'([\+\-]?)\$?([0-9,]+\.?[0-9]*)', balance_text)
                    if balance_match:
                        try:
                            sign = balance_match.group(1)
                            balance_str = balance_match.group(2).replace(',', '')
                            balance = float(balance_str)
                            balance = -balance if sign == '-' else balance
                        except (ValueError, AttributeError):
                            balance = None

        return balance

    async def async_get_estimated_days_remaining(self) -> Optional[int]:
        """Get the estimated days remaining from the services page."""
        await self._ensure_login()
        resp = await self._session.get(BASE_URL / "services")
        resp.raise_for_status()
        html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")

        days_remaining = None

        # Look for the specific estimated days remaining structure
        # Target: <dt>Estimated Days Remaining ... </dt><dd><span class="text-success">27</span></dd>
        # Note: dt may contain nested HTML elements, so we need to search by text content
        all_dts = soup.find_all("dt")
        days_dt = None
        for dt in all_dts:
            if re.search(r"Estimated\s+Days\s+Remaining", dt.get_text(), re.I):
                days_dt = dt
                break

        if days_dt:
            dd_days = days_dt.find_next("dd")
            if dd_days:
                # Look for the days span within the dd element
                days_span = dd_days.find("span")
                if days_span:
                    days_text = days_span.get_text(strip=True)

                    # Extract numeric value from text like "27"
                    days_match = re.search(r'(\d+)', days_text)
                    if days_match:
                        try:
                            days_remaining = int(days_match.group(1))
                        except (ValueError, AttributeError):
                            days_remaining = None

        return days_remaining

    async def async_get_usage(self, user_id: str, day: int = 1) -> Optional[dict]:
        """Scrape today's and month-to-date data usage (GB) from /usage.

        The monthly graph is rendered inline as an ECharts option. Daily values
        live in per-series ``data`` arrays keyed by name:
          - 'Data Usage Down'
          - 'Data Usage Up'
          - 'Data Usage Night (Free)'  (absent on plans without a free-night tier)
        The chart title carries the authoritative month-to-date total.

        ``day`` is the local day-of-month (1-31); the value at index day-1 is
        today's bar. Days later in the month read 0.00 until they arrive.

        Returns a dict of GB figures, or None if the page couldn't be parsed
        (so the caller can keep the last known values).
        """
        await self._ensure_login()

        async def _fetch() -> str:
            resp = await self._session.get(BASE_URL / "usage", params={"userid": str(user_id)})
            resp.raise_for_status()
            return await resp.text()

        html = await _fetch()

        # Session cookie may have lapsed during long-running polling; the portal
        # then serves the login page. Re-authenticate once and retry.
        if 'name="username"' in html:
            self._logged_in = False
            await self.async_login()
            html = await _fetch()

        down = _echarts_series(html, "Data Usage Down")
        up = _echarts_series(html, "Data Usage Up")
        night = _echarts_series(html, "Data Usage Night (Free)")

        if not down and not up:
            # Page layout changed or usage not yet available; signal "no data".
            return None

        idx = day - 1

        def _at(series: list[Optional[float]]) -> float:
            if 0 <= idx < len(series) and series[idx] is not None:
                return float(series[idx])
            return 0.0

        d, u, n = _at(down), _at(up), _at(night)

        # Month-to-date total: prefer the chart title (matches the portal exactly),
        # fall back to summing the daily series.
        m = re.search(r"Total Usage\s*([\d.]+)\s*GB", html)
        if m:
            month_total = float(m.group(1))
        else:
            month_total = round(
                sum(v for v in (down + up + night) if v is not None), 2
            )

        return {
            "today_gb": round(d + u + n, 2),
            "download_gb": round(d, 2),
            "upload_gb": round(u, 2),
            "night_free_gb": round(n, 2),
            "month_to_date_gb": round(month_total, 2),
        }
