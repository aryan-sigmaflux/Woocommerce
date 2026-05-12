"""
Address state handlers — saved address selection and new address collection.
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CheckoutSession
from bot import session_manager
from clients import whatsapp as wa_client

logger = logging.getLogger(__name__)


# ── Entry Point ─────────────────────────────────────────────────────


async def enter_address_check(db: AsyncSession, session: CheckoutSession):
    """
    Transition to ADDRESS_CHECK.
    Check for saved addresses and present options.
    """
    phone = session.phone
    addresses = await session_manager.get_addresses_for_phone(db, phone)

    if not addresses:
        from config import settings
        if settings.WA_FLOW_ID:
            await session_manager.update_session_state(db, session, "ADDRESS_CHECK")
            await wa_client.send_flow(
                to=phone,
                body="📍 Please fill out your delivery address to continue.",
                flow_id=settings.WA_FLOW_ID,
                header="Delivery Details",
                button_text="Enter Address"
            )
        else:
            # Fallback to old line-by-line collection
            await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_NAME")
            await wa_client.send_text(phone, "📍 Let's set up your delivery address.\n\n*Full name for delivery?*")
        return

    if len(addresses) == 1:
        # One saved address — ask to use it or enter new
        addr = addresses[0]
        addr_text = _format_address(addr)

        await session_manager.update_session_state(db, session, "ADDRESS_CHECK")
        await wa_client.send_buttons(
            to=phone,
            body=(
                f"Welcome back! 👋\n"
                f"Deliver to your saved address?\n\n"
                f"{addr_text}"
            ),
            buttons=[
                {"id": f"addr_use_{addr.id}", "title": "✅ Yes, deliver here"},
                {"id": "addr_new", "title": "📝 New address"},
            ],
        )
        return

    # Multiple saved addresses — use list message
    await session_manager.update_session_state(db, session, "ADDRESS_CHECK")
    rows = []
    for addr in addresses[:9]:  # Max 10 rows including "new address"
        label = addr.label or "Address"
        rows.append({
            "id": f"addr_use_{addr.id}",
            "title": label[:24],
            "description": f"{addr.line1}, {addr.city} - {addr.pincode}"[:72],
        })
    rows.append({
        "id": "addr_new",
        "title": "📝 Enter new address",
        "description": "Type a brand new delivery address",
    })

    await wa_client.send_list(
        to=phone,
        body="Welcome back! 👋\nWhere should we deliver your order?",
        button_text="Choose Address",
        sections=[{"title": "Saved Addresses", "rows": rows}],
    )


# ── ADDRESS_CHECK Handler ──────────────────────────────────────────


async def handle_address_check(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
    list_id: str = None,
):
    """Handle address selection or new address request."""
    phone = session.phone
    selection = button_id or list_id or ""

    if selection == "addr_new":
        from config import settings
        if settings.WA_FLOW_ID:
            await wa_client.send_flow(
                to=phone, body="📍 Please fill out your delivery address.",
                flow_id=settings.WA_FLOW_ID, header="Delivery Details", button_text="Enter Address"
            )
        else:
            await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_NAME")
            await wa_client.send_text(phone, "*Full name for delivery?*")
        return

    if message_text and message_text.startswith("FLOW_SUBMIT:"):
        # Handle the form submission from the WhatsApp Flow
        import json
        payload_str = message_text.replace("FLOW_SUBMIT:", "")
        try:
            data = json.loads(payload_str)
            
            # The structure of `response_json` typically nests data in `values` or directly
            # but usually NFM reply looks like `{"full_name": "...", "pincode": "..."}` 
            # if we bound the payload directly to the footer button.
            if "full_name" not in data and "values" in data:
                # Meta sometimes wraps it
                data = data["values"]

            full_name = data.get("full_name", "")
            email = data.get("email", "")
            alt_phone = data.get("alternate_number", "")
            line1 = data.get("house_street", "")
            landmark = data.get("landmark", "")
            city = data.get("city", "")
            state = data.get("state", "")
            pincode = str(data.get("pincode", ""))
            
            # Validate required fields
            if not full_name or not line1 or not pincode:
                await wa_client.send_text(phone, "⚠️ Form was incomplete. Please tap 'Enter Address' to try again.")
                return

            # Double-check pincode serviceability (in case it differs from initial check)
            from utils.pincode_service import is_pincode_serviceable
            if not is_pincode_serviceable(pincode):
                await wa_client.send_text(
                    phone,
                    f"😔 *Sorry, delivery is not available at pincode {pincode}.*\n\n"
                    "Please enter an address in our serviceable area."
                )
                return

            # Store email persistently in the cart object so the confirm state can read it
            if email:
                cart_copy = list(session.cart) if session.cart else []
                # Check if meta dictionary exists at the end
                if not cart_copy or not isinstance(cart_copy[-1], dict) or "_meta" not in cart_copy[-1]:
                    cart_copy.append({"_meta": True, "email": email})
                else:
                    cart_copy[-1]["email"] = email
                session.cart = cart_copy
                
            # If they provided an alt phone, append it to line2 or landmark
            line2_parts = []
            if landmark: line2_parts.append(landmark)
            if alt_phone: line2_parts.append(f"Alt: {alt_phone}")
            line2 = " | ".join(line2_parts)

            # Save the address directly to the DB! (Skipping confirmation state for flows)
            address = await session_manager.save_address(
                db=db,
                phone=phone,
                full_name=full_name,
                line1=line1,
                line2=line2 or None,
                city=city,
                state=state,
                pincode=pincode
            )

            await session_manager.update_session_state(
                db, session, "ADDRESS_CONFIRMED",
                selected_address_id=address.id,
            )

            addr_text = _format_address(address)
            await wa_client.send_text(phone, f"✅ Address saved!\n\n{addr_text}")

            # Advance to coupon prompt
            from bot.states.coupon import enter_coupon_prompt
            await enter_coupon_prompt(db, session)
            return
            
        except Exception as e:
            logger.error(f"[ADDRESS] Flow parse error: {e}")
            await wa_client.send_text(phone, "⚠️ Failed to read the form. Please try again.")
            return

    if selection.startswith("addr_use_"):
        address_id_str = selection.replace("addr_use_", "")
        try:
            address_id = UUID(address_id_str)
            address = await session_manager.get_address_by_id(db, address_id)
            if address:
                await session_manager.set_address_as_last_used(db, phone, address_id)
                await session_manager.update_session_state(
                    db, session, "ADDRESS_CONFIRMED",
                    selected_address_id=address_id,
                )
                addr_text = _format_address(address)
                await wa_client.send_text(phone, f"✅ Delivering to:\n\n{addr_text}")

                # Advance to coupon prompt
                from bot.states.coupon import enter_coupon_prompt
                await enter_coupon_prompt(db, session)
                return
        except (ValueError, Exception) as e:
            logger.error(f"[ADDRESS] Invalid address ID: {e}")

    # Unexpected input
    count = await session_manager.increment_unexpected_input(db, session)
    if count >= 5:
        await wa_client.send_text(phone, '🔄 Type "RESTART" to start over.')
    else:
        await wa_client.send_text(phone, "Please select an option from the message above 👆")


# ── Address Collection State Handlers ───────────────────────────────


async def handle_collecting_name(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """COLLECTING_ADDRESS_NAME — get full name."""
    phone = session.phone
    name = message_text.strip()

    if not name or len(name) < 2:
        await wa_client.send_text(phone, "Please enter a valid full name.")
        return

    # Store name temporarily in cart metadata (we'll use it when saving)
    session.cart = _set_temp_address(session.cart, "full_name", name)
    await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_LINE1")
    await wa_client.send_text(phone, "*House / Flat / Building?*")


async def handle_collecting_line1(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """COLLECTING_ADDRESS_LINE1 — get address line 1."""
    phone = session.phone
    line1 = message_text.strip()

    if not line1 or len(line1) < 3:
        await wa_client.send_text(phone, "Please enter a valid address.")
        return

    session.cart = _set_temp_address(session.cart, "line1", line1)
    await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_LINE2")
    await wa_client.send_text(phone, "*Street / Area?* (type 'skip' to skip)")


async def handle_collecting_line2(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """COLLECTING_ADDRESS_LINE2 — get address line 2 (optional)."""
    phone = session.phone
    text = message_text.strip()

    if text.lower() == "skip":
        session.cart = _set_temp_address(session.cart, "line2", "")
    else:
        session.cart = _set_temp_address(session.cart, "line2", text)

    await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_CITY")
    await wa_client.send_text(phone, "*City?*")


async def handle_collecting_city(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """COLLECTING_ADDRESS_CITY — get city."""
    phone = session.phone
    city = message_text.strip()

    if not city or len(city) < 2:
        await wa_client.send_text(phone, "Please enter a valid city name.")
        return

    session.cart = _set_temp_address(session.cart, "city", city)
    await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_STATE")
    await wa_client.send_text(phone, "*State?*")


async def handle_collecting_state(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """COLLECTING_ADDRESS_STATE — get state."""
    phone = session.phone
    state_val = message_text.strip()

    if not state_val or len(state_val) < 2:
        await wa_client.send_text(phone, "Please enter a valid state name.")
        return

    session.cart = _set_temp_address(session.cart, "state", state_val)
    await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_PINCODE")
    await wa_client.send_text(phone, "*Pincode?*")


async def handle_collecting_pincode(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
):
    """COLLECTING_ADDRESS_PINCODE — get pincode, then confirm."""
    phone = session.phone
    pincode = message_text.strip()

    if not pincode or len(pincode) < 4 or not pincode.isdigit():
        await wa_client.send_text(phone, "Please enter a valid pincode (digits only).")
        return

    session.cart = _set_temp_address(session.cart, "pincode", pincode)

    # Show confirmation
    temp = _get_temp_address(session.cart)
    addr_preview = (
        f"{temp.get('full_name', '')}\n"
        f"{temp.get('line1', '')}"
    )
    if temp.get("line2"):
        addr_preview += f", {temp['line2']}"
    addr_preview += (
        f"\n{temp.get('city', '')}, {temp.get('state', '')} - {temp.get('pincode', '')}"
    )

    await session_manager.update_session_state(db, session, "ADDRESS_CONFIRM")
    await wa_client.send_buttons(
        to=phone,
        body=f"📍 *Confirming delivery address:*\n\n{addr_preview}",
        buttons=[
            {"id": "addr_confirm", "title": "✅ Correct"},
            {"id": "addr_reenter", "title": "🔄 Re-enter"},
        ],
    )


async def handle_address_confirm(
    db: AsyncSession,
    session: CheckoutSession,
    message_text: str,
    button_id: str = None,
):
    """ADDRESS_CONFIRM — user confirms or re-enters address."""
    phone = session.phone

    if button_id == "addr_reenter":
        # Clear temp address and restart collection
        session.cart = _clear_temp_address(session.cart)
        await session_manager.update_session_state(db, session, "COLLECTING_ADDRESS_NAME")
        await wa_client.send_text(phone, "*Full name for delivery?*")
        return

    if button_id == "addr_confirm":
        # Save the address
        temp = _get_temp_address(session.cart)
        address = await session_manager.save_address(
            db=db,
            phone=phone,
            full_name=temp.get("full_name", ""),
            line1=temp.get("line1", ""),
            line2=temp.get("line2") or None,
            city=temp.get("city", ""),
            state=temp.get("state", ""),
            pincode=temp.get("pincode", ""),
        )

        # Clear temp and set selected address
        session.cart = _clear_temp_address(session.cart)
        await session_manager.update_session_state(
            db, session, "ADDRESS_CONFIRMED",
            selected_address_id=address.id,
        )

        await wa_client.send_text(phone, "✅ Address saved!")

        # Advance to coupon prompt
        from bot.states.coupon import enter_coupon_prompt
        await enter_coupon_prompt(db, session)
        return

    # Unexpected input
    count = await session_manager.increment_unexpected_input(db, session)
    if count >= 5:
        await wa_client.send_text(phone, '🔄 Type "RESTART" to start over.')
    else:
        await wa_client.send_text(phone, "Please tap ✅ Correct or 🔄 Re-enter.")


# ── Helpers ─────────────────────────────────────────────────────────


def _format_address(addr) -> str:
    """Format an Address model into a display string."""
    label = f"📍 {addr.label} — " if addr.label else "📍 "
    parts = [addr.line1]
    if addr.line2:
        parts.append(addr.line2)
    line = ", ".join(parts)
    return f"{label}{addr.full_name}\n{line}\n{addr.city}, {addr.state} - {addr.pincode}"


def _set_temp_address(cart: list, key: str, value: str) -> list:
    """
    Store temp address field in the cart JSONB.
    We use a special dict at the end of the cart list with key '_temp_address'.
    """
    import copy
    # Ensure cart is a new list with new dicts to trigger SQLAlchemy JSONB update
    cart_copy = copy.deepcopy(cart) if cart else []

    # Find or create temp address entry
    for item in cart_copy:
        if isinstance(item, dict) and item.get("_temp_address"):
            item[key] = value
            return cart_copy

    cart_copy.append({"_temp_address": True, key: value})
    return cart_copy


def _get_temp_address(cart: list) -> dict:
    """Extract the temp address dict from cart."""
    for item in (cart or []):
        if isinstance(item, dict) and item.get("_temp_address"):
            return item
    return {}


def _clear_temp_address(cart: list) -> list:
    """Remove the temp address entry from cart."""
    return [item for item in (cart or []) if not (isinstance(item, dict) and item.get("_temp_address"))]
