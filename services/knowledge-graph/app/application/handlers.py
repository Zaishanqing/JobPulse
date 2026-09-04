from dataclasses import dataclass

from app.application import (
    AnalyzeDependenciesUseCase,
    AssessJDQualityUseCase,
    AutoReviewBuildUseCase,
    BuildGraphUseCase,
    BuildJobUseCase,
    BatchReviewTasksUseCase,
    DependencyReferenceUseCase,
    ClaimReviewTaskUseCase,
    CompleteReviewTaskUseCase,
    ConfirmExtractionUseCase,
    CompareBuildWatermarksUseCase,
    CreateMappingCandidateUseCase,
    CreateReviewTaskUseCase,
    ExtractJDUseCase,
    ImportExtractionResultUseCase,
    ImportJDUseCase,
    ImportCapabilitySkillSnapshotUseCase,
    ImportNormalizedResultUseCase,
    ImportPublishedJDFactUseCase,
    ModifyRelationUseCase,
    NormalizeJDUseCase,
    OpenGraphDraftUseCase,
    PublishGraphVersionUseCase,
    RebuildProjectionUseCase,
    ReviewMappingCandidateUseCase,
    ReviewDependencyCandidateUseCase,
    ResolveUnresolvedSkillUseCase,
    RollbackGraphVersionUseCase,
    UpdateAlgorithmConfigUseCase,
    UpsertJDUseCase,
)


@dataclass(frozen=True)
class ApplicationHandlers:
    import_capability_skill: ImportCapabilitySkillSnapshotUseCase
    import_jd: ImportJDUseCase
    upsert_jd: UpsertJDUseCase
    import_published_fact: ImportPublishedJDFactUseCase
    extract_jd: ExtractJDUseCase
    import_extraction: ImportExtractionResultUseCase
    confirm_extraction: ConfirmExtractionUseCase
    normalize_jd: NormalizeJDUseCase
    import_normalized: ImportNormalizedResultUseCase
    assess_quality: AssessJDQualityUseCase
    resolve_skill: ResolveUnresolvedSkillUseCase
    build_graph: BuildGraphUseCase
    build_jobs: BuildJobUseCase
    open_graph_draft: OpenGraphDraftUseCase
    create_review_task: CreateReviewTaskUseCase
    claim_review_task: ClaimReviewTaskUseCase
    complete_review_task: CompleteReviewTaskUseCase
    batch_review_tasks: BatchReviewTasksUseCase
    auto_review_build: AutoReviewBuildUseCase
    modify_relation: ModifyRelationUseCase
    publish_graph: PublishGraphVersionUseCase
    rollback_graph: RollbackGraphVersionUseCase
    update_algorithm_config: UpdateAlgorithmConfigUseCase
    create_mapping_candidate: CreateMappingCandidateUseCase
    review_mapping_candidate: ReviewMappingCandidateUseCase
    analyze_dependencies: AnalyzeDependenciesUseCase
    review_dependency_candidate: ReviewDependencyCandidateUseCase
    rebuild_projection: RebuildProjectionUseCase
    compare_watermarks: CompareBuildWatermarksUseCase
    dependency_references: DependencyReferenceUseCase
