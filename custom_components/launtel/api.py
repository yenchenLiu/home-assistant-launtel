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
            pause_button = card.find("button", onclick=re.compile(r"(un)?pauseService\((\d+)") )
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
        
        # Look for the specific balance structure in the Launtel portal
        # Target: <dt>Current Balance</dt><dd><span class="text-success">+$112.65</span></dd>
        current_balance_dt = soup.find("dt", string=re.compile(r"Current\s+Balance", re.I))
        if current_balance_dt:
            dd_balance = current_balance_dt.find_next("dd")
            if dd_balance:
                # Look for the balance span within the dd element
                balance_span = dd_balance.find("span")
                if balance_span:
                    balance_text = balance_span.get_text(strip=True)
                    
                    # Extract numeric value from text like "+$112.65" or "-$50.00"
                    balance_match = re.search(r'([\+\-]?)\$?([0-9,]+\.?[0-9]*)', balance_text)
                    if balance_match:
                        try:
                            sign = balance_match.group(1)
                            balance_str = balance_match.group(2).replace(',', '')
                            balance = float(balance_str)
                            
                            # Handle negative balances
                            if sign == '-':
                                balance = -balance
                        except (ValueError, AttributeError):
                            balance = None
        
        # Fallback: look for balance card structure
        if balance is None:
            balance_card = soup.find("div", class_="card-balance")
            if balance_card:
                balance_dd = balance_card.find("dd")
                if balance_dd:
                    balance_span = balance_dd.find("span")
                    if balance_span:
                        balance_text = balance_span.get_text(strip=True)
                        
                        balance_match = re.search(r'([\+\-]?)\$?([0-9,]+\.?[0-9]*)', balance_text)
                        if balance_match:
                            try:
                                sign = balance_match.group(1)
                                balance_str = balance_match.group(2).replace(',', '')
                                balance = float(balance_str)
                                
                                if sign == '-':
                                    balance = -balance
                            except (ValueError, AttributeError):
                                balance = None
        
        # Final fallback: search for "Current Balance" text pattern in page
        if balance is None:
            page_text = soup.get_text()
            
            # Look for "Current Balance +$112.65" pattern
            balance_pattern = r'Current\s+Balance[:\s]*([\+\-]?)\$?([0-9,]+\.?[0-9]*)'
            match = re.search(balance_pattern, page_text, re.IGNORECASE)
            if match:
                try:
                    sign = match.group(1)
                    balance_str = match.group(2).replace(',', '')
                    balance = float(balance_str)
                    
                    if sign == '-':
                        balance = -balance
                except (ValueError, AttributeError):
                    balance = None
        
        return balance
