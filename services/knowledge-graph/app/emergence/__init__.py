"""EMERGE v3.2 final production policy package.

Promoted verbatim from EXP-EMERGE-01 experiments:
  - ``emergence_v2``: v2.1 two-stage policy engine (Stage1 relations,
    Stage2 emergence states, skill index, BGE responsibility alignment);
  - ``emergence_v3_1``: six-layer temporal evidence + mature-reference bank
    and top-k explanation (Stage1 final semantics);
  - ``emergence_v3_2``: Stage2 occupation-cluster acceptance (JD =
    observation, cluster = analysis unit);
  - ``policy``: formal production facade ``EmergenceV32Policy``.

This is a versioned algorithm package promoted from experiments; it is not
part of the layered core (``app.domain`` / ``app.application``) so the core
architecture contracts stay untouched.  The legacy five-dimension v1 chain
(``app.domain.novelty``) is archived and no longer the active policy.
"""

from app.emergence.emergence_v3_1 import (
    ReferenceProfile,
    TemporalLayers,
    build_candidate_temporal_layers,
    build_cluster_temporal_layers,
    build_mature_reference_bank,
    candidate_independent_market_stats,
    explain_relation_with_reference,
    explain_with_reference_ranking,
    occupation_key,
    retrieve_top_k,
)
from app.emergence.emergence_v3_2 import (
    EXPLAINABLE_STRUCTURAL_RELATIONS,
    STABLE_RELATIONS,
    STRUCTURAL_RELATIONS,
    UNEXPLAINED_COMBINED_MAX,
    UNEXPLAINED_RELATION,
    cluster_stage2_decision,
    cluster_stage2_with_ablations,
    retrieve_top_k_cached,
)
from app.emergence.policy import EmergenceV32Policy

__all__ = [
    "EXPLAINABLE_STRUCTURAL_RELATIONS",
    "ReferenceProfile",
    "STABLE_RELATIONS",
    "STRUCTURAL_RELATIONS",
    "TemporalLayers",
    "UNEXPLAINED_COMBINED_MAX",
    "UNEXPLAINED_RELATION",
    "EmergenceV32Policy",
    "build_candidate_temporal_layers",
    "build_cluster_temporal_layers",
    "build_mature_reference_bank",
    "candidate_independent_market_stats",
    "cluster_stage2_decision",
    "cluster_stage2_with_ablations",
    "explain_relation_with_reference",
    "explain_with_reference_ranking",
    "occupation_key",
    "retrieve_top_k",
    "retrieve_top_k_cached",
]
