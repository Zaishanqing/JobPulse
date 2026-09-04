"""Deprecated test-fixture compatibility export.

Production routes use ``ManageTasks`` through the task Port.  This module owns no
task rule, ORM operation, transaction, or HTTP mapping.
"""

from app.infrastructure.task_compat import create_task_compat as create_task

__all__ = ["create_task"]
