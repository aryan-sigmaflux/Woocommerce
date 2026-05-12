"""
Exponential backoff retry decorator for API calls.
"""

import asyncio
import functools
import logging

logger = logging.getLogger(__name__)


def async_retry(max_retries: int = 3, base_delay: float = 5.0, multiplier: float = 3.0):
    """
    Decorator for async functions — retries on exception with exponential backoff.
    Default delays: 5s → 15s → 45s
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (multiplier ** (attempt - 1))
                        logger.warning(
                            f"[RETRY] {func.__name__} attempt {attempt}/{max_retries} failed: {e}. "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"[RETRY] {func.__name__} failed after {max_retries} attempts: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator
