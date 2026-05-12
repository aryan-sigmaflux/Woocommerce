"""
Reset.py
Run this script manually to delete all data (sessions, addresses, messages)
for a specific user phone number from the database.
"""

import asyncio
import sys
from sqlalchemy import delete

from db.database import async_session_factory
from db.models import CheckoutSession, Address, MessageLog
from utils.pincode_service import clear_awaiting_pincode

async def reset_user(phone: str):
    print(f"Starting reset for phone number: {phone} ...")
    try:
        async with async_session_factory() as db:
            # 1. Delete message logs for this phone
            print("  -> Deleting message logs...")
            await db.execute(delete(MessageLog).where(MessageLog.phone == phone))
            
            # 2. Delete checkout sessions for this phone
            print("  -> Deleting checkout sessions...")
            await db.execute(delete(CheckoutSession).where(CheckoutSession.phone == phone))
            
            # 3. Delete addresses for this phone
            print("  -> Deleting saved addresses...")
            await db.execute(delete(Address).where(Address.phone == phone))
            
            await db.commit()
            
        # 4. Clear any in-memory pincode cache
        clear_awaiting_pincode(phone)
            
        print(f"\n✅ Successfully deleted all records and reset state for {phone}.")

    except Exception as e:
        print(f"\n❌ Failed to reset user: {e}")

if __name__ == "__main__":
    # Default phone number requested
    target_phone = "919326169639"
    
    # Allow passing a different phone number as a command-line argument
    if len(sys.argv) > 1:
        target_phone = sys.argv[1]
    
    asyncio.run(reset_user(target_phone))
