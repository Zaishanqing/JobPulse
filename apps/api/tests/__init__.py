"""Local test support package.

Making ``tests`` an explicit package prevents an unrelated third-party
``tests`` package from shadowing ``tests.runtime_database`` during collection
and session cleanup.
"""
