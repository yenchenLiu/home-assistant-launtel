from __future__ import annotations

import asyncio
from typing import Optional
from aiohttp import ClientSession

import typer

# Reuse the API client from the integration without requiring Home Assistant
from custom_components.launtel.api import LauntelClient

app = typer.Typer(help="Launtel CLI - inspect and change your Launtel residential service plans")


async def _get_client(username: str, password: str) -> tuple[LauntelClient, ClientSession]:
    session = ClientSession()
    client = LauntelClient(session, username, password)
    await client.async_login()
    return client, session


def _run(coro):
    return asyncio.run(coro)


@app.command()
def services(
    username: str = typer.Option(..., "--username", "-u", envvar="LAUNTEL_USERNAME", help="Launtel username"),
    password: str = typer.Option(..., "--password", "-p", envvar="LAUNTEL_PASSWORD", help="Launtel password"),
):
    """List services available in your Launtel account."""

    async def _list():
        client, session = await _get_client(username, password)
        try:
            svcs = await client.async_get_services()
            if not svcs:
                typer.echo("No services found")
                return
            for s in svcs:
                typer.echo(
                    f"service_id={s.service_id}\tTitle={s.title}\tAVCID={s.avcid}\tUserID={s.user_id}\tSpeed='{s.speed_label or ''}'\tChangeInProgress={s.change_in_progress}"
                )
        finally:
            await session.close()

    _run(_list())


@app.command()
def plans(
    service_id: Optional[int] = typer.Option(None, "--service-id", "-s", help="Service ID to inspect"),
    avcid: Optional[str] = typer.Option(None, "--avcid", help="AVCID (if you already know it)"),
    username: str = typer.Option(..., "--username", "-u", envvar="LAUNTEL_USERNAME", help="Launtel username"),
    password: str = typer.Option(..., "--password", "-p", envvar="LAUNTEL_PASSWORD", help="Launtel password"),
):
    """Show current plan and available options for a service."""

    async def _show():
        client, session = await _get_client(username, password)
        try:
            target_avcid = avcid
            if not target_avcid:
                svcs = await client.async_get_services()
                match = next((s for s in svcs if s.service_id == service_id), None)
                if not match:
                    typer.secho("Service not found", fg=typer.colors.RED)
                    raise typer.Exit(code=2)
                target_avcid = match.avcid
                typer.echo(f"Service: {match.title} (service_id={match.service_id}, avcid={match.avcid})")
            opts, label_to_psid, current_label, locid, plans_mapping = await client.async_get_plan_options(target_avcid)
            typer.echo(f"Current plan: {current_label or 'Unknown'}")
            typer.echo("Options:")
            if not opts:
                typer.echo("  (none available or change in progress)")
            for i, label in enumerate(opts, start=1):
                psid = label_to_psid.get(label)
                meta = plans_mapping.get(psid, {}) if psid is not None else {}
                price = meta.get("price_per_day")
                speed = meta.get("speed")
                unlimited = meta.get("unlimited")
                typer.echo(f"  {i}. {label}  [psid={psid}, price/day={price}, speed={speed}, unlimited={unlimited}]")
            if locid:
                typer.echo(f"locid={locid}")
        finally:
            await session.close()

    if not (service_id or avcid):
        typer.secho("You must provide either --service-id or --avcid", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    _run(_show())


@app.command("change-plan")
def change_plan(
    option: Optional[str] = typer.Option(None, "--label", help="Plan label to switch to (exact match)"),
    psid: Optional[int] = typer.Option(None, "--psid", help="Plan psid to switch to (overrides --label)"),
    service_id: Optional[int] = typer.Option(None, "--service-id", "-s", help="Service ID to change"),
    avcid: Optional[str] = typer.Option(None, "--avcid", help="AVCID (if you already know it)"),
    username: str = typer.Option(..., "--username", "-u", envvar="LAUNTEL_USERNAME", help="Launtel username"),
    password: str = typer.Option(..., "--password", "-p", envvar="LAUNTEL_PASSWORD", help="Launtel password"),
):
    """Submit a plan change by label or psid."""

    async def _change():
        client, session = await _get_client(username, password)
        try:
            # Resolve service and avcid
            target_service_id = service_id
            target_avcid = avcid
            user_id: Optional[str] = None
            if not target_avcid or not target_service_id:
                svcs = await client.async_get_services()
                match = None
                if target_service_id is not None:
                    match = next((s for s in svcs if s.service_id == target_service_id), None)
                elif target_avcid is not None:
                    match = next((s for s in svcs if s.avcid == target_avcid), None)
                if not match:
                    typer.secho("Service not found", fg=typer.colors.RED)
                    raise typer.Exit(code=2)
                target_service_id = match.service_id
                target_avcid = match.avcid
                user_id = match.user_id
            # Fetch options for locid and mapping
            opts, label_to_psid, current_label, locid, plans_mapping = await client.async_get_plan_options(target_avcid)
            target_psid = psid
            if target_psid is None:
                if not option:
                    typer.secho("Provide --psid or --label", fg=typer.colors.RED)
                    raise typer.Exit(code=2)
                target_psid = label_to_psid.get(option)
                if target_psid is None:
                    typer.secho("Label not found in available options", fg=typer.colors.RED)
                    raise typer.Exit(code=2)
            if not locid or not user_id:
                # Resolve user_id if needed
                if not user_id:
                    svcs = await client.async_get_services()
                    m = next((s for s in svcs if s.avcid == target_avcid), None)
                    user_id = m.user_id if m else None
                if not locid or not user_id:
                    typer.secho("Unable to resolve locid or user_id (portal may be changing)", fg=typer.colors.RED)
                    raise typer.Exit(code=3)
            await client.async_change_plan(
                user_id=user_id,
                psid=target_psid,
                service_id=target_service_id,
                avcid=target_avcid,
                locid=locid,
            )
            typer.secho("Plan change submitted. Portal may show 'Change in progress' for a while.", fg=typer.colors.GREEN)
        finally:
            await session.close()

    if psid is None and not option:
        typer.secho("Provide --psid or --label", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not (service_id or avcid):
        typer.secho("You must provide either --service-id or --avcid", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    _run(_change())


if __name__ == "__main__":
    app()

