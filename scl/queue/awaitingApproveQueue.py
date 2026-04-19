"""
AwaitingApproveQueue Module

Background:
For any Task or CapTask instance, if it's approval is False.
Which means, it should in one of AwaitingApproveQueue instance.

Design Goals & Features:
Supports Task or CapTask instance been added and removed from this instance.
It supports to be registered with a notice function.
If the this instance is empty, for any get(as remove) request return None.
Human will randomly pick one Task or CapTask instance from this instance to approve.
If any instance's approval is True, it should moved to the beginning of this queue and waiting to be removed.
Once the move happened, the notice function will be called.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.
- Just impl necessary functions.
"""

import logging
import threading
from collections import deque
from typing import Callable, Dict, List, Optional, Union

from opentelemetry import trace
from scl.otel.otel import meter, tracer

from scl.meta.task import Task
from scl.meta.captask import CapTask

logger = logging.getLogger(__name__)

# Metrics
queue_add_counter = meter.create_counter(
    "awaiting_approve_queue.added",
    description="Number of items added to the awaiting approve queue"
)
queue_get_counter = meter.create_counter(
    "awaiting_approve_queue.removed",
    description="Number of items removed from the awaiting approve queue via get()"
)
queue_approve_counter = meter.create_counter(
    "awaiting_approve_queue.approved",
    description="Number of items approved (moved to front)"
)
queue_size_gauge = meter.create_up_down_counter(
    "awaiting_approve_queue.size",
    description="Current number of items in the awaiting approve queue"
)
notifier_invoked_counter = meter.create_counter(
    "awaiting_approve_queue.notifier_invoked",
    description="Number of times the registered notifier was invoked"
)


class AwaitingApproveQueue:
    """
    A queue that holds Task or CapTask instances with approval == False.
    Human operators can pick items randomly from the queue for approval.
    When an item is approved (approval becomes True), it is moved to the front
    of the queue and will be returned by the next get() call.
    A notifier function can be registered and is called when an item is moved
    to the front after approval.

    Example usage:
        from scl.meta.task import Task
        from scl.meta.captask import CapTask
        from scl.queue.awaiting_approve_queue import AwaitingApproveQueue

        def on_approved_item_moved():
            print("An approved item is now at the front of the queue!")

        # Create queue and register notifier
        queue = AwaitingApproveQueue()
        queue.register_notifier(on_approved_item_moved)

        # Add a task that needs approval
        task = Task(system_prompt="Do something", approval=False)
        queue.add(task)

        # Human reviews pending items and picks one to approve
        pending = queue.get_pending_items()
        print(f"Pending items: {pending}")
        chosen = pending[0]  # human picks randomly in real scenario

        # Approve the chosen item
        queue.approve(chosen.hash)   # This moves it to the front and calls notifier

        # Now the approved item can be retrieved
        approved_item = queue.get()
        print(f"Retrieved: {approved_item}")
    """

    def __init__(self):
        """Initialize the queue and internal structures."""
        self._deque: deque = deque()
        self._items_by_hash: Dict[str, Union[Task, CapTask]] = {}
        self._lock = threading.RLock()
        self._notifier: Optional[Callable[[], None]] = None

        logger.info("AwaitingApproveQueue initialized")

    def register_notifier(self, callback: Callable[[], None]) -> None:
        """
        Register a notifier function to be called when an item is approved
        and moved to the front of the queue.

        Args:
            callback: A callable that takes no arguments.
        """
        with self._lock:
            self._notifier = callback
        logger.info("Notifier function registered for AwaitingApproveQueue")

    def _invoke_notifier(self) -> None:
        """Invoke the registered notifier, if any, handling exceptions."""
        notifier = self._notifier
        if notifier is not None:
            try:
                logger.debug("Invoking notifier")
                notifier()
                notifier_invoked_counter.add(1)
            except Exception as e:
                logger.error(f"Notifier raised an exception: {e}", exc_info=True)

    @tracer.start_as_current_span("AwaitingApproveQueue.add")
    def add(self, item: Union[Task, CapTask]) -> bool:
        """
        Add a Task or CapTask to the queue if its approval is False.
        Items with approval=True are ignored.

        Args:
            item: The Task or CapTask instance to add.

        Returns:
            True if the item was added, False otherwise.
        """
        current_span = trace.get_current_span()
        item_hash = getattr(item, 'hash', None)
        item_type = type(item).__name__
        current_span.set_attribute("item.type", item_type)
        current_span.set_attribute("item.hash", item_hash)
        current_span.set_attribute("item.approval", item.approval)

        if item.approval:
            logger.debug(f"{item_type} {item_hash} already approved; not adding to queue")
            current_span.set_attribute("queue.added", False)
            return False

        with self._lock:
            if item_hash in self._items_by_hash:
                logger.debug(f"{item_type} {item_hash} already in queue; skipping")
                current_span.set_attribute("queue.added", False)
                return False

            self._deque.append(item)
            self._items_by_hash[item_hash] = item
            queue_add_counter.add(1, {"item_type": item_type})
            queue_size_gauge.add(1)

        logger.info(f"{item_type} {item_hash} added to awaiting approve queue")
        logger.debug(f"Queue size: {len(self._deque)}")
        current_span.set_attribute("queue.added", True)
        current_span.set_attribute("queue.size", len(self._deque))
        return True

    @tracer.start_as_current_span("AwaitingApproveQueue.get")
    def get(self) -> Optional[Union[Task, CapTask]]:
        """
        Remove and return the item at the front of the queue.
        Typically this will be an approved item waiting to be processed.

        Returns:
            The item, or None if the queue is empty.
        """
        current_span = trace.get_current_span()

        with self._lock:
            if not self._deque:
                logger.debug("Queue is empty; get returns None")
                current_span.set_attribute("queue.empty", True)
                return None

            item = self._deque.popleft()
            item_hash = getattr(item, 'hash', None)
            del self._items_by_hash[item_hash]

            item_type = type(item).__name__
            queue_get_counter.add(1, {"item_type": item_type})
            queue_size_gauge.add(-1)

        logger.info(f"{item_type} {item_hash} removed from awaiting approve queue")
        logger.debug(f"Queue size after get: {len(self._deque)}")
        current_span.set_attribute("item.type", item_type)
        current_span.set_attribute("item.hash", item_hash)
        current_span.set_attribute("queue.size", len(self._deque))
        return item

    @tracer.start_as_current_span("AwaitingApproveQueue.approve")
    def approve(self, item_hash: str) -> bool:
        """
        Mark the item with the given hash as approved (sets approval=True),
        and move it to the front of the queue. The registered notifier will be
        called upon successful move.

        Args:
            item_hash: The hash of the item to approve.

        Returns:
            True if the item was found and moved, False otherwise.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("item.hash", item_hash)

        with self._lock:
            item = self._items_by_hash.get(item_hash)
            if item is None:
                logger.warning(f"Item with hash {item_hash} not found in queue")
                current_span.set_attribute("queue.approved", False)
                return False

            # Update approval status
            item.approval = True
            item_type = type(item).__name__

            # Remove from current position
            self._deque.remove(item)
            # Place at front
            self._deque.appendleft(item)

            queue_approve_counter.add(1, {"item_type": item_type})

        logger.info(f"{item_type} {item_hash} approved and moved to front of queue")
        logger.debug(f"Queue size: {len(self._deque)}")
        current_span.set_attribute("queue.approved", True)
        current_span.set_attribute("item.type", item_type)

        # Notify that an approved item is now at the front
        self._invoke_notifier()
        return True

    def get_pending_items(self) -> List[Union[Task, CapTask]]:
        """
        Return a list of all items currently in the queue (pending approval).
        Human operators can randomly pick from this list to approve.

        Returns:
            List of pending Task or CapTask instances.
        """
        with self._lock:
            return list(self._deque)

    def size(self) -> int:
        """Return the current number of items in the queue."""
        with self._lock:
            return len(self._deque)

    def contains(self, item_hash: str) -> bool:
        """Check if an item with the given hash is in the queue."""
        with self._lock:
            return item_hash in self._items_by_hash


# Missing / Future Features (kept as comments for open-source tracking):
# - Support for batch approval.
# - Persistent storage of queue state.
# - Priority levels beyond the approved-to-front mechanism.
# - Configurable notification throttling.