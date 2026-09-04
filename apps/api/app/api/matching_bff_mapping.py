"""Public compatibility facade for matching BFF mapping helpers.

Domain helpers live under ``app.api.matching_bff``; this module keeps the
historical public entry point importable without changing the BFF contract.
"""

from app.api.matching_bff.common import *  # noqa: F401,F403
from app.api.matching_bff.evidence import *  # noqa: F401,F403
from app.api.matching_bff.learning_path import *  # noqa: F401,F403
from app.api.matching_bff.gap import *  # noqa: F401,F403
from app.api.matching_bff.evaluation import *  # noqa: F401,F403
from app.api.matching_bff.what_if import *  # noqa: F401,F403
