"""
Async queue system.
Tasks are processed one-at-a-time to avoid flooding Telegram / B2.
"""

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class QueueManager:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._processor: Optional[Callable] = None
        self._client = None
        self._is_processing: bool = False
        self._current_task_label: str = "None"

    def set_processor(self, func: Callable):
        """Register the async function that processes each task."""
        self._processor = func

    def set_client(self, client):
        self._client = client

    async def add_to_queue(self, task: dict) -> bool:
        """Add a task dict to the queue. Returns False if queue is full."""
        if self._queue.full():
            return False
        await self._queue.put(task)
        logger.info(f"[Queue] Task added — queue size: {self._queue.qsize()}")
        return True

    async def process_queue(self):
        """
        Infinite loop that picks tasks one-at-a-time and calls _processor.
        Run this as an asyncio task at startup.
        """
        logger.info("[Queue] Worker started.")
        while True:
            task = await self._queue.get()
            self._is_processing = True
            anime_title = task.get("anime", {}).get("title", "Unknown")
            self._current_task_label = anime_title
            logger.info(f"[Queue] Processing: {anime_title}")
            try:
                await self._processor(task)
            except Exception as e:
                logger.error(f"[Queue] Processor error for {anime_title}: {e}")
            finally:
                self._is_processing = False
                self._current_task_label = "None"
                self._queue.task_done()

    def get_status(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "is_processing": self._is_processing,
            "current_task": self._current_task_label,
        }

    def clear(self):
        """Empty the queue (use with caution)."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        logger.info("[Queue] Cleared.")
