"""A20 skill_decomposition 测试。"""

import pytest
from app.domain.skill_decomposition import (
    SkillDecomposition,
    SemanticContext,
    TopologicalContext,
    EvidenceContext,
    decompose_skill_change,
    decompose_version_pair,
    compute_pair_summary,
    compute_cross_pair_analysis,
    _category_neighbors,
    _jaccard,
    _compute_context_similarity,
    _compute_evidence_delta,
)


# ===== helper to build test relations =====

def _rel(
    skill_id="java",
    name="Java",
    cat="LANG",
    weight=0.8,
    src=3,
    ent=5,
    sup=20,
):
    return {
        "skill_id": skill_id,
        "canonical_name": name,
        "category_code": cat,
        "weight": weight,
        "final_weight": weight,
        "statistics": {
            "source_diversity": src,
            "enterprise_coverage": ent,
            "support_document_count": sup,
        },
    }


# ===== unit tests =====

class TestJaccard:
    def test_identical_sets(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_both_empty(self):
        assert _jaccard(set(), set()) == 1.0


class TestContextSimilarity:
    def test_identical_context(self):
        assert _compute_context_similarity("LANG", "LANG", "Java", "Java") == 1.0

    def test_category_changed_name_same(self):
        assert _compute_context_similarity("LANG", "FRAMEWORK", "Spring", "Spring") == 0.5

    def test_name_substring(self):
        assert _compute_context_similarity(
            "LANG", "LANG", "Java", "JavaScript",
        ) == pytest.approx(0.75)  # 0.5 cat + 0.25 partial name

    def test_both_changed(self):
        assert _compute_context_similarity("LANG", "INFRA", "Java", "Docker") == 0.0


class TestEvidenceDelta:
    def test_no_change(self):
        b = {"source_diversity": 3, "enterprise_coverage": 5, "support_document_count": 20}
        a = {"source_diversity": 3, "enterprise_coverage": 5, "support_document_count": 20}
        assert _compute_evidence_delta(b, a, 100, 100) == 0.0

    def test_source_change(self):
        b = {"source_diversity": 1, "enterprise_coverage": 5, "support_document_count": 20}
        a = {"source_diversity": 4, "enterprise_coverage": 5, "support_document_count": 20}
        delta = _compute_evidence_delta(b, a, 100, 100)
        assert delta > 0.2  # 3/4 = 0.75 for source, /3 = 0.25

    def test_all_changed(self):
        b = {"source_diversity": 1, "enterprise_coverage": 1, "support_document_count": 5}
        a = {"source_diversity": 5, "enterprise_coverage": 10, "support_document_count": 50}
        delta = _compute_evidence_delta(b, a, 100, 100)
        assert delta > 0.4  # Significant evidence change


class TestCategoryNeighbors:
    def test_finds_same_category_skills(self):
        all_skills = [
            _rel("java", "Java", "LANG"),
            _rel("python", "Python", "LANG"),
            _rel("docker", "Docker", "INFRA"),
        ]
        neighbors = _category_neighbors("LANG", all_skills, "java")
        assert neighbors == {"python"}

    def test_excludes_self(self):
        all_skills = [_rel("java", "Java", "LANG")]
        neighbors = _category_neighbors("LANG", all_skills, "java")
        assert neighbors == set()

    def test_empty_category(self):
        all_skills = [_rel("docker", "Docker", "INFRA")]
        neighbors = _category_neighbors("LANG", all_skills, "java")
        assert neighbors == set()


# ===== decomposition tests =====

class TestDecomposeSkillChange:
    def test_no_change_all_stable(self):
        before = _rel("java", "Java", "LANG", weight=0.8, src=3, ent=5, sup=20)
        after = _rel("java", "Java", "LANG", weight=0.8, src=3, ent=5, sup=20)
        all_skills = [before, _rel("python", "Python", "LANG")]

        d = decompose_skill_change(before, after, all_skills, all_skills)

        assert d.weight_delta == 0.0
        assert d.context_similarity == 1.0
        assert not d.category_changed
        assert not d.name_changed
        assert d.neighborhood_jaccard == 1.0
        assert d.evidence_delta_normalized == 0.0
        # With zero weight delta, residual should be 1.0 (nothing to explain)
        assert d.residual == 1.0

    def test_category_migration_detected(self):
        before = _rel("tensorflow", "TensorFlow", "AI", weight=0.5, src=3, ent=5, sup=20)
        after = _rel("tensorflow", "TensorFlow", "FRAMEWORK", weight=0.3, src=3, ent=5, sup=20)
        all_before = [before, _rel("pytorch", "PyTorch", "AI")]
        all_after = [after, _rel("docker", "Docker", "FRAMEWORK")]

        d = decompose_skill_change(before, after, all_before, all_after)

        assert d.category_changed
        assert d.community_migrated
        assert d.context_similarity == 0.5  # cat changed, name same
        assert d.semantic_contribution > 0.15
        # Neighborhood: before had pytorch in AI, after has docker in FRAMEWORK
        assert d.neighborhood_jaccard == 0.0  # completely different neighbors

    def test_name_change_detected(self):
        before = _rel("js", "JavaScript", "LANG", weight=0.4, src=2, ent=3, sup=10)
        after = _rel("js", "TypeScript", "LANG", weight=0.5, src=2, ent=3, sup=10)
        all_skills = [before, after, _rel("python", "Python", "LANG")]

        d = decompose_skill_change(before, after, all_skills, all_skills)

        assert d.name_changed
        assert not d.category_changed
        assert d.context_similarity < 1.0

    def test_evidence_driven_change(self):
        before = _rel("java", "Java", "LANG", weight=0.3, src=1, ent=1, sup=3)
        after = _rel("java", "Java", "LANG", weight=0.8, src=5, ent=10, sup=50)
        all_skills = [before, after, _rel("python", "Python", "LANG")]

        d = decompose_skill_change(before, after, all_skills, all_skills)

        assert d.evidence_delta_normalized > 0.3
        assert d.context_similarity == 1.0
        assert d.neighborhood_jaccard == 1.0
        assert d.weight_delta == pytest.approx(0.5)

    def test_pure_market_signal_high_residual(self):
        """权重变化但语义、拓扑、证据均不变 → residual 接近 1.0。"""
        before = _rel("kafka", "Kafka", "INFRA", weight=0.3, src=2, ent=5, sup=15)
        after = _rel("kafka", "Kafka", "INFRA", weight=0.7, src=2, ent=5, sup=15)
        all_skills = [before, after, _rel("redis", "Redis", "INFRA")]

        d = decompose_skill_change(before, after, all_skills, all_skills)

        assert d.context_similarity == 1.0
        assert d.neighborhood_jaccard == 1.0
        assert d.evidence_delta_normalized == 0.0
        assert d.residual > 0.8  # mostly unexplained → genuine market change

    def test_dominant_factor_correct(self):
        before = _rel("go", "Go", "LANG", weight=0.2, src=1, ent=1, sup=2)
        after = _rel("go", "Go", "INFRA", weight=0.6, src=1, ent=1, sup=2)
        all_before = [before, _rel("java", "Java", "LANG")]
        all_after = [after, _rel("docker", "Docker", "INFRA")]

        d = decompose_skill_change(before, after, all_before, all_after)

        assert d.category_changed
        assert d.dominant_factor in ("semantic", "topological")

    def test_is_explained_by_artifact_false_for_market_change(self):
        before = _rel("python", "Python", "LANG", weight=0.5, src=3, ent=5, sup=20)
        after = _rel("python", "Python", "LANG", weight=0.8, src=3, ent=5, sup=20)
        all_skills = [before, after, _rel("java", "Java", "LANG")]

        d = decompose_skill_change(before, after, all_skills, all_skills)
        assert not d.is_explained_by_artifact  # residual should dominate

    def test_missing_stats_handled_gracefully(self):
        before = {
            "skill_id": "java", "canonical_name": "Java",
            "category_code": "LANG", "weight": 0.5,
        }
        after = {
            "skill_id": "java", "canonical_name": "Java",
            "category_code": "LANG", "weight": 0.7,
            "statistics": {},
        }
        all_skills = [before, after]

        d = decompose_skill_change(before, after, all_skills, all_skills)
        assert d.evidence_delta_normalized == 0.0
        assert d.residual > 0.8

    def test_skills_not_in_both_versions_excluded(self):
        before = _rel("java", "Java", "LANG", weight=0.5)
        after = _rel("python", "Python", "LANG", weight=0.5)
        all_skills = [before, after]

        d = decompose_skill_change(before, after, all_skills, all_skills)
        # weight_delta may be non-zero but this tests edge: different skill_ids
        # In real use, decompose_version_pair handles this by matching skill_id
        pass  # This test verifies the function doesn't crash with mismatched IDs


# ===== version pair tests =====

class TestDecomposeVersionPair:
    def test_common_skills_decomposed(self):
        before_snap = {
            "skill_relations": [
                _rel("java", "Java", "LANG", weight=0.8, src=3, ent=5, sup=20),
                _rel("python", "Python", "LANG", weight=0.6, src=2, ent=4, sup=15),
                _rel("docker", "Docker", "INFRA", weight=0.4, src=2, ent=3, sup=10),
            ],
            "sample_stats": {"included_samples": 50},
        }
        after_snap = {
            "skill_relations": [
                _rel("java", "Java", "INFRA", weight=0.5, src=3, ent=5, sup=18),
                _rel("python", "Python", "LANG", weight=0.7, src=2, ent=4, sup=16),
                # docker removed — should be excluded
            ],
            "sample_stats": {"included_samples": 55},
        }

        results = decompose_version_pair(before_snap, after_snap)

        assert len(results) == 2  # java & python
        java_result = next(r for r in results if r.skill_id == "java")
        assert java_result.category_changed
        assert java_result.community_migrated

    def test_small_weight_delta_filtered(self):
        before_snap = {
            "skill_relations": [
                _rel("java", "Java", "LANG", weight=0.800, src=3, ent=5, sup=20),
            ],
            "sample_stats": {"included_samples": 50},
        }
        after_snap = {
            "skill_relations": [
                _rel("java", "Java", "LANG", weight=0.801, src=3, ent=5, sup=20),
            ],
            "sample_stats": {"included_samples": 50},
        }

        results = decompose_version_pair(before_snap, after_snap)
        # delta is 0.001, below 0.005 threshold → filtered
        assert len(results) == 0

    def test_empty_snapshot_handled(self):
        before_snap = {"skill_relations": [], "sample_stats": {"included_samples": 0}}
        after_snap = {"skill_relations": [], "sample_stats": {"included_samples": 0}}

        results = decompose_version_pair(before_snap, after_snap)
        assert results == []


class TestPairSummary:
    def test_summary_computed(self):
        decomps = [
            SkillDecomposition(
                skill_id="java", canonical_name_before="Java", canonical_name_after="Java",
                weight_before=0.8, weight_after=0.5, weight_delta=-0.3,
                context_similarity=0.5, category_changed=True, name_changed=False,
                neighborhood_jaccard=0.3, community_migrated=True,
                evidence_delta_normalized=0.1,
                semantic_contribution=0.4, topological_contribution=0.2,
                evidence_contribution=0.1, residual=0.3,
                explanation="category migrated: LANG→INFRA",
            ),
            SkillDecomposition(
                skill_id="python", canonical_name_before="Python", canonical_name_after="Python",
                weight_before=0.5, weight_after=0.6, weight_delta=0.1,
                context_similarity=1.0, category_changed=False, name_changed=False,
                neighborhood_jaccard=1.0, community_migrated=False,
                evidence_delta_normalized=0.0,
                semantic_contribution=0.0, topological_contribution=0.0,
                evidence_contribution=0.0, residual=1.0,
                explanation="unexplained market signal",
            ),
        ]

        summary = compute_pair_summary(decomps)

        assert summary["total_skills_compared"] == 2
        assert summary["artifact_driven_count"] == 1
        assert summary["market_driven_count"] == 1
        assert summary["community_migration_count"] == 1
        assert summary["avg_semantic_contribution"] == pytest.approx(0.2)
        assert summary["avg_residual"] == pytest.approx(0.65)

    def test_empty_summary(self):
        summary = compute_pair_summary([])
        assert summary["total_skills_compared"] == 0


# ===== cross-pair analysis tests =====

class TestCrossPairAnalysis:
    def test_tracks_skill_across_pairs(self):
        d1 = SkillDecomposition(
            skill_id="java", canonical_name_before="Java", canonical_name_after="Java",
            weight_before=0.8, weight_after=0.6, weight_delta=-0.2,
            context_similarity=0.5, category_changed=True, name_changed=False,
            neighborhood_jaccard=0.3, community_migrated=True,
            evidence_delta_normalized=0.1,
            semantic_contribution=0.5, topological_contribution=0.3,
            evidence_contribution=0.1, residual=0.1,
            explanation="category migrated",
        )
        d2 = SkillDecomposition(
            skill_id="java", canonical_name_before="Java", canonical_name_after="Java",
            weight_before=0.6, weight_after=0.65, weight_delta=0.05,
            context_similarity=1.0, category_changed=False, name_changed=False,
            neighborhood_jaccard=1.0, community_migrated=False,
            evidence_delta_normalized=0.0,
            semantic_contribution=0.0, topological_contribution=0.0,
            evidence_contribution=0.0, residual=1.0,
            explanation="unexplained",
        )

        analysis = compute_cross_pair_analysis([
            ("v1→v2", [d1]),
            ("v2→v3", [d2]),
        ])

        assert analysis["skills_tracked"] == 1
        # java's dominant factor changed: semantic→residual → variable
        assert analysis["variable_dominant_factor"] == 1
        assert analysis["consistent_dominant_factor"] == 0
        assert len(analysis["migratory_skills"]) == 1
        assert analysis["migratory_skills"][0]["migration_count"] == 1

    def test_consistent_skill(self):
        d1 = SkillDecomposition(
            skill_id="python", canonical_name_before="Python", canonical_name_after="Python",
            weight_before=0.5, weight_after=0.55, weight_delta=0.05,
            context_similarity=1.0, category_changed=False, name_changed=False,
            neighborhood_jaccard=1.0, community_migrated=False,
            evidence_delta_normalized=0.0,
            semantic_contribution=0.0, topological_contribution=0.0,
            evidence_contribution=0.0, residual=1.0,
            explanation="unexplained",
        )
        d2 = SkillDecomposition(
            skill_id="python", canonical_name_before="Python", canonical_name_after="Python",
            weight_before=0.55, weight_after=0.6, weight_delta=0.05,
            context_similarity=1.0, category_changed=False, name_changed=False,
            neighborhood_jaccard=1.0, community_migrated=False,
            evidence_delta_normalized=0.0,
            semantic_contribution=0.0, topological_contribution=0.0,
            evidence_contribution=0.0, residual=1.0,
            explanation="unexplained",
        )

        analysis = compute_cross_pair_analysis([
            ("v1→v2", [d1]),
            ("v2→v3", [d2]),
        ])

        assert analysis["consistent_dominant_factor"] == 1
        assert analysis["variable_dominant_factor"] == 0


# ===== edge cases =====

class TestEdgeCases:
    def test_weight_on_boundary(self):
        """Very small non-zero weight delta still produces valid decomposition."""
        before = _rel("rust", "Rust", "LANG", weight=0.01, src=1, ent=1, sup=1)
        after = _rel("rust", "Rust", "LANG", weight=0.02, src=1, ent=1, sup=2)
        all_skills = [before, after]

        d = decompose_skill_change(before, after, all_skills, all_skills)
        assert abs(d.weight_delta) > 0
        assert 0 <= d.residual <= 1.0

    def test_category_other_to_specific(self):
        before = _rel("llm", "LLM", "OTHER", weight=0.2, src=1, ent=1, sup=5)
        after = _rel("llm", "大模型", "AI", weight=0.6, src=3, ent=4, sup=30)
        all_before = [before]
        all_after = [after, _rel("pytorch", "PyTorch", "AI")]

        d = decompose_skill_change(before, after, all_before, all_after)
        assert d.category_changed
        assert d.name_changed
        assert d.context_similarity == 0.0  # both cat & name changed

    def test_single_skill_in_category(self):
        """Skill is the only one in its category — neighborhood is empty both sides."""
        before = _rel("kafka", "Kafka", "BIGDATA", weight=0.3, src=1, ent=1, sup=5)
        after = _rel("kafka", "Kafka", "BIGDATA", weight=0.5, src=2, ent=3, sup=10)
        all_skills = [before, after]

        d = decompose_skill_change(before, after, all_skills, all_skills)
        # Both empty neighbor sets → Jaccard = 1.0
        assert d.neighborhood_jaccard == 1.0
