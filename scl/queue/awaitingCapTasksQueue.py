"""
AwaitingCapTasksQueue Module

Background:
For any Task instance, if it has any CapTask which in created status, and approval is True.
Which means, it should in one of AwaitingCapTasksQueue instance.

Design Goals & Features:
It works as a heap.
Supports Task instance been add and removed from this heap.
reverse order by how many CapTask waiting for status not in created.
- Which means on the top of the heap, the Task instance's CapTasks are all in Processed status.

If the heap is empty, return None.
It supports to be registered with a notice function.
- Once added, if there is a notice function, then it will be invoked.

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
- Dependencies are documented as `pip install` commands, not requirements.txt.
"""

import heapq
import logging
import threading
from typing import Callable, List, Optional, Set, Tuple

from opentelemetry import trace
from scl.otel.otel import meter, tracer

from scl.meta.task import Task
from scl.meta.captask import CapTask

logger = logging.getLogger(__name__)

# Metrics
heap_push_counter = meter.create_counter(
    "awaiting_queue.task_pushed",
    description="Number of tasks pushed to the awaiting heap"
)
heap_pop_counter = meter.create_counter(
    "awaiting_queue.task_popped",
    description="Number of tasks popped from the awaiting heap"
)
heap_remove_counter = meter.create_counter(
    "awaiting_queue.task_removed",
    description="Number of tasks explicitly removed from the awaiting heap"
)
heap_size_gauge = meter.create_up_down_counter(
    "awaiting_queue.size",
    description="Current number of tasks in the awaiting heap"
)
notifier_invoked_counter = meter.create_counter(
    "awaiting_queue.notifier_invoked",
    description="Number of times the registered notifier was invoked"
)


class AwaitingCapTasksQueue:
    """
    A priority queue (min-heap) that holds Task instances which have at least one
    CapTask in 'created' status with approval=True. The priority is the number
    of such CapTasks. The top of the heap contains Tasks with the fewest pending
    CapTasks (ideally 0, meaning all CapTasks are processed).

    If the heap is empty, pop() returns None.

    A notifier function can be registered; it will be called whenever a new Task
    is successfully pushed onto the heap.

    Example usage:
        from scl.meta.task import Task
        from scl.meta.captask import CapTask
        from awaiting_captasks_queue import AwaitingCapTasksQueue

        def my_notifier():
            print("New task added!")

        # Create a Task with CapTasks
        task = Task(system_prompt="Process data", capacity=["cpu"])
        cap1 = CapTask(cap_name="step1", args=[], approval=True, status="created")
        cap2 = CapTask(cap_name="step2", args=[], approval=True, status="Processed")
        task.add_cap_task(cap1)
        task.add_cap_task(cap2)

        # Queue management
        queue = AwaitingCapTasksQueue()
        queue.register_notifier(my_notifier)

        queue.push(task)   # Adds and invokes notifier

        # Retrieve the task with fewest pending CapTasks
        next_task = queue.pop()  # Returns task or None if empty

        # Update task after a CapTask status changes
        cap1.set_status("Processed")
        queue.update(task)   # Re-prioritize (notifier is NOT called for updates)
    """

    def __init__(self):
        """Initialize an empty heap with a lock for thread safety."""
        self._heap: List[Tuple[int, str, Task]] = []
        self._task_set: Set[str] = set()  # Track task hashes for O(1) membership check
        self._lock = threading.RLock()
        self._notifier: Optional[Callable[[], None]] = None

        logger.info("AwaitingCapTasksQueue initialized")

    def register_notifier(self, callback: Callable[[], None]) -> None:
        """
        Register a notifier function to be called whenever a Task is successfully
        added to the heap (via push()).

        Args:
            callback: A callable that takes no arguments.
        """
        with self._lock:
            self._notifier = callback
        logger.info("Notifier function registered for AwaitingCapTasksQueue")

    def _invoke_notifier(self) -> None:
        """Invoke the registered notifier, if any. Handles exceptions gracefully."""
        notifier = None
        with self._lock:
            notifier = self._notifier
        if notifier is not None:
            try:
                logger.debug("Invoking notifier")
                notifier()
                notifier_invoked_counter.add(1)
            except Exception as e:
                logger.error(f"Notifier raised an exception: {e}", exc_info=True)

    def _compute_priority(self, task: Task) -> int:
        """
        Count the number of CapTasks in the given Task that are in 'created' status
        and have approval=True.

        Returns:
            The priority value (lower is better, meaning fewer pending tasks).
        """
        count = 0
        for cap in task.cap_tasks:
            if cap.approval and cap.status == "created":
                count += 1
        return count

    @tracer.start_as_current_span("AwaitingCapTasksQueue.push")
    def push(self, task: Task) -> bool:
        """
        Add a Task to the heap if it contains at least one pending CapTask
        ('created' and approval=True). If the task is already in the heap,
        it will be updated instead.

        If the task is successfully added, the registered notifier (if any)
        is invoked.

        Args:
            task: The Task instance to add.

        Returns:
            True if the task was added or updated, False if it had no pending CapTasks.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("task.hash", task.hash)

        priority = self._compute_priority(task)
        if priority == 0:
            logger.debug(f"Task {task.hash} has no pending CapTasks; not adding to heap")
            current_span.set_attribute("queue.pushed", False)
            return False

        with self._lock:
            # If task already exists, remove its old entry first
            if task.hash in self._task_set:
                self._remove_locked(task.hash)

            # Push new entry
            entry = (priority, task.hash, task)
            heapq.heappush(self._heap, entry)
            self._task_set.add(task.hash)
            heap_push_counter.add(1, {"priority": priority})
            heap_size_gauge.add(1)
            logger.info(f"Task {task.hash} pushed to heap with priority {priority} (pending CapTasks)")
            logger.debug(f"Heap size: {len(self._heap)}")

        current_span.set_attribute("queue.pushed", True)
        current_span.set_attribute("queue.priority", priority)

        # Notify after successful addition
        self._invoke_notifier()
        return True

    @tracer.start_as_current_span("AwaitingCapTasksQueue.pop")
    def pop(self) -> Optional[Task]:
        """
        Remove and return the Task with the smallest priority (fewest pending CapTasks).
        If the heap is empty, returns None.

        Returns:
            The Task instance or None.
        """
        current_span = trace.get_current_span()

        with self._lock:
            if not self._heap:
                logger.debug("Heap is empty; pop returns None")
                current_span.set_attribute("queue.empty", True)
                return None

            priority, task_hash, task = heapq.heappop(self._heap)
            self._task_set.remove(task_hash)
            heap_pop_counter.add(1, {"priority": priority})
            heap_size_gauge.add(-1)

            logger.info(f"Task {task.hash} popped from heap (priority {priority})")
            logger.debug(f"Heap size after pop: {len(self._heap)}")

        current_span.set_attribute("task.hash", task.hash)
        current_span.set_attribute("queue.priority", priority)
        return task

    @tracer.start_as_current_span("AwaitingCapTasksQueue.remove")
    def remove(self, task: Task) -> bool:
        """
        Explicitly remove a Task from the heap.

        Args:
            task: The Task instance to remove.

        Returns:
            True if the task was found and removed, False otherwise.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("task.hash", task.hash)

        with self._lock:
            removed = self._remove_locked(task.hash)
            if removed:
                heap_remove_counter.add(1)
                heap_size_gauge.add(-1)
                logger.info(f"Task {task.hash} removed from heap")
                current_span.set_attribute("queue.removed", True)
            else:
                logger.debug(f"Task {task.hash} not found in heap")
                current_span.set_attribute("queue.removed", False)

        return removed

    def _remove_locked(self, task_hash: str) -> bool:
        """
        Internal method to remove an entry by task hash. Assumes lock is held.
        Rebuilds the heap after removal (O(n) approach).

        Returns:
            True if removed, False if not found.
        """
        for i, (_, h, _) in enumerate(self._heap):
            if h == task_hash:
                # Remove the element at index i
                self._heap[i] = self._heap[-1]
                self._heap.pop()
                self._task_set.remove(task_hash)
                # Restore heap property
                if i < len(self._heap):
                    heapq.heapify(self._heap)
                return True
        return False

    @tracer.start_as_current_span("AwaitingCapTasksQueue.update")
    def update(self, task: Task) -> bool:
        """
        Update the position of a Task in the heap after its CapTasks have changed.
        This is equivalent to removing and re-adding the task with its new priority.

        Note: The notifier is NOT invoked for updates (only for initial push).

        Args:
            task: The Task instance to update.

        Returns:
            True if the task was in the heap and updated, or newly added;
            False if it had no pending CapTasks and was not in the heap.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("task.hash", task.hash)

        priority = self._compute_priority(task)
        with self._lock:
            was_present = task.hash in self._task_set
            if was_present:
                self._remove_locked(task.hash)
                logger.debug(f"Task {task.hash} removed during update")

            if priority > 0:
                # Re-add with new priority
                entry = (priority, task.hash, task)
                heapq.heappush(self._heap, entry)
                self._task_set.add(task.hash)
                heap_push_counter.add(1, {"priority": priority})
                if not was_present:
                    heap_size_gauge.add(1)
                logger.info(f"Task {task.hash} updated in heap with priority {priority}")
                current_span.set_attribute("queue.updated", True)
                current_span.set_attribute("queue.priority", priority)
                return True
            else:
                # No pending CapTasks, so task should not be in heap
                if was_present:
                    heap_size_gauge.add(-1)
                logger.info(f"Task {task.hash} removed from heap (no pending CapTasks)")
                current_span.set_attribute("queue.updated", False)
                return False

    def peek(self) -> Optional[Task]:
        """
        Return the Task at the top of the heap without removing it.

        Returns:
            The Task instance or None if heap is empty.
        """
        with self._lock:
            if self._heap:
                return self._heap[0][2]
            return None

    def size(self) -> int:
        """Return the current number of tasks in the heap."""
        with self._lock:
            return len(self._heap)

    def contains(self, task: Task) -> bool:
        """Check if a Task is currently in the heap."""
        with self._lock:
            return task.hash in self._task_set


# Missing / Future Features (kept as comments for open-source tracking):
# - Persistent storage of heap state for crash recovery.
# - Batch operations for efficiency when updating many tasks at once.
# - Custom comparator support if Task priority model changes.
# - Notification mechanism when a task becomes "all processed" (priority 0).