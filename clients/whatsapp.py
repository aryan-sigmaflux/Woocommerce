"""
Meta Cloud API client for sending WhatsApp messages.
Supports text, interactive buttons, interactive lists, and template messages.
"""

import logging
from typing import Optional

import httpx

from config import settings
from utils.retry import async_retry

logger = logging.getLogger(__name__)

_GRAPH_URL = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.META_PHONE_NUMBER_ID}/messages"
_HEADERS = {
    "Authorization": f"Bearer {settings.META_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30.0)


@async_retry()
async def _send(payload: dict) -> dict:
    """Send a message payload to the Meta Cloud API."""
    async with _client() as client:
        r = await client.post(_GRAPH_URL, json=payload, headers=_HEADERS)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"[WA] Meta API Error Payload: {r.text}")
            raise e
            
        data = r.json()
        wamid = data.get("messages", [{}])[0].get("id")
        logger.info(f"[WA] Message sent to {payload.get('to')} | wamid={wamid}")
        return data


# ── Text Message ────────────────────────────────────────────────────


async def send_text(to: str, body: str, preview_url: bool = False) -> dict:
    """Send a plain text message."""
    return await _send({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": preview_url, "body": body},
    })


# ── Interactive Flow Message ──────────────────────────────────────

async def send_flow(to: str, body: str, flow_id: str, flow_token: str = "FLOW_TOKEN", button_text: str = "Open Form", header: str = None, footer: str = None) -> dict:
    """Send an interactive WhatsApp Flow message."""
    interactive = {
        "type": "flow",
        "header": {"type": "text", "text": header} if header else None,
        "body": {"text": body},
        "footer": {"text": footer} if footer else None,
        "action": {
            "name": "flow",
            "parameters": {
                "flow_message_version": "3",
                "flow_token": flow_token,
                "flow_id": flow_id,
                "flow_cta": button_text,
                "flow_action": "navigate",
                "flow_action_payload": {"screen": "ADDRESS_FORM"}
            }
        }
    }

    # Clean None values in top level interactive
    interactive = {k: v for k, v in interactive.items() if v is not None}

    return await _send({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


# ── Interactive Button Reply ────────────────────────────────────────


async def send_buttons(to: str, body: str, buttons: list[dict], header: str = None, footer: str = None) -> dict:
    """
    Send an interactive button message (up to 3 buttons).
    
    buttons: [{"id": "btn_1", "title": "Button Text"}, ...]
    """
    interactive = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}}
                for btn in buttons[:3]
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    return await _send({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


# ── Interactive List Message ────────────────────────────────────────


async def send_list(
    to: str,
    body: str,
    button_text: str,
    sections: list[dict],
    header: str = None,
    footer: str = None,
) -> dict:
    """
    Send an interactive list message (up to 10 items).
    
    sections: [{
        "title": "Section Title",
        "rows": [{"id": "row_1", "title": "Row Title", "description": "Optional"}]
    }]
    """
    interactive = {
        "type": "list",
        "body": {"text": body},
        "action": {
            "button": button_text[:20],
            "sections": sections,
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    return await _send({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


# ── Template Message ────────────────────────────────────────────────


async def send_template(
    to: str,
    template_name: str,
    language_code: str = "en",
    components: list[dict] = None,
) -> dict:
    """
    Send an approved WhatsApp template message.
    
    components: list of template component objects (header, body params, etc.)
    """
    template = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template["components"] = components

    return await _send({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    })


# ── Catalog Message ─────────────────────────────────────────────────


async def send_catalog(
    to: str,
    body: str = "🛍️ Browse our full catalog below!",
    thumbnail_product_retailer_id: str = None,
    footer: str = None,
) -> dict:
    """
    Send the entire WhatsApp Business catalog to the user.
    Uses the interactive catalog_message type.
    
    thumbnail_product_retailer_id: retailer_id of a product to show as the thumbnail.
    """
    interactive = {
        "type": "catalog_message",
        "body": {"text": body},
        "action": {
            "name": "catalog_message",
        },
    }

    if thumbnail_product_retailer_id:
        interactive["action"]["parameters"] = {
            "thumbnail_product_retailer_id": thumbnail_product_retailer_id,
        }

    if footer:
        interactive["footer"] = {"text": footer}

    return await _send({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


# ── Mark as Read ────────────────────────────────────────────────────


async def mark_as_read(wamid: str) -> None:
    """Mark a received message as read (blue ticks)."""
    try:
        async with _client() as client:
            await client.post(
                _GRAPH_URL,
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": wamid,
                },
                headers=_HEADERS,
            )
    except Exception as e:
        logger.warning(f"[WA] Failed to mark as read: {e}")
