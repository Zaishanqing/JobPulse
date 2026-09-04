"""Deprecated import alias for trend-analysis adapter fault-injection hooks."""

import sys

from app.infrastructure import trend_analysis as _adapter


sys.modules[__name__] = _adapter
