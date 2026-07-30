"""Webhook management (under the System page)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request

from ..services import webhooks
from ..templating import redirect

router = APIRouter(prefix="/system/webhooks")


@router.post("/add")
async def add(request: Request, url: str = Form(...), secret: str = Form("")):
    # Events render as one checkbox per key; only the ticked ones submit, so the
    # selected set is exactly the checkboxes named after event keys.
    form = await request.form()
    events = [e.key for e in webhooks.EVENTS if form.get(e.key)]
    ok, message = webhooks.add_webhook(url, events, secret)
    return redirect("/system#webhooks", message, "success" if ok else "error")


@router.post("/{hook_id}/delete")
def delete(hook_id: str):
    ok = webhooks.delete_webhook(hook_id)
    return redirect(
        "/system#webhooks",
        "Webhook removed." if ok else "Webhook not found.",
        "success" if ok else "error",
    )


@router.post("/{hook_id}/toggle")
def toggle(hook_id: str, enabled: bool = Form(...)):
    ok = webhooks.set_enabled(hook_id, enabled)
    state = "enabled" if enabled else "disabled"
    return redirect(
        "/system#webhooks",
        f"Webhook {state}." if ok else "Webhook not found.",
        "success" if ok else "error",
    )


@router.post("/{hook_id}/test")
def test(hook_id: str):
    ok, message = webhooks.send_test(hook_id)
    return redirect("/system#webhooks", message, "success" if ok else "error")
