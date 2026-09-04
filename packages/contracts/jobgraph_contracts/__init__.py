'''Framework-neutral, versioned contracts shared by JobGraph services.'''

from jobgraph_contracts.catalog import (
    PositionReference,
    StandardPositionRef,
    StandardSkillRef,
    StandardSkillSnapshotV1,
    StandardSkillSnapshotV2,
)
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.discovery import DiscoveryJDSnapshotV2
from jobgraph_contracts.errors import ContractErrorCode
from jobgraph_contracts.extraction_bundle import (
    ExtractedJDBundleV1,
    ExtractedJDBundleV2,
    parse_extracted_jd_bundle,
    validate_bundle_matches_envelope,
)
from jobgraph_contracts.extraction_v2 import JDExtractionResult
from jobgraph_contracts.evidence import Evidence
from jobgraph_contracts.normalization_v2 import JDNormalizedResult
from jobgraph_contracts.requirement_graph import (
    RequirementGraph,
    RequirementGraphChild,
    RequirementGraphGroup,
)
from jobgraph_contracts.published_jd import (
    CatalogSnapshotRefV1,
    PositionCatalogSnapshotRefV1,
    PublishedJDFactV3,
    ValidationLineageV2,
    build_published_jd_fact_v3,
)
from jobgraph_contracts.release_manifest import (
    ReleaseArtifactV1,
    ReleaseManifestV1,
    ReleaseMode,
)
from jobgraph_contracts.skill_relations import (
    SkillRelationSnapshotV1,
    SkillRelationSnapshotV2,
)
from jobgraph_contracts.source_identity import (
    build_source_key,
    ensure_timezone_aware,
    normalize_source_record_id,
    parse_crawl_time,
    validate_source_platform,
)
from jobgraph_contracts.position_profile import (
    PositionProfileV1,
    PositionProfileV2,
    PositionProfileV3,
)
from jobgraph_contracts.review import (
    EvidenceContextV1,
    ReviewBatchOperationV1,
    ReviewBatchResultV1,
    ReviewTaskPageV1,
    ReviewTaskV1,
)
from jobgraph_contracts.demo_manifest import CompetitionDemoManifestV1
from jobgraph_contracts.execution_modes import (
    EXECUTION_MODE_SEMANTICS,
    ExecutionMode,
    ExecutionModeResultV1,
)
from jobgraph_contracts.rag import (
    BusinessObjectRef,
    EvidenceRAGCoverageV1,
    EvidenceRAGQueryV1,
    EvidenceRAGResponseV1,
    PermissionContextV1,
    RAGErrorV1,
    RAGEvidenceReferenceV1,
    RAGVersionScope,
)

__all__ = [
    'build_source_key',
    'PositionProfileV1',
    'PositionProfileV2',
    'PositionProfileV3',
    'EvidenceContextV1',
    'ReviewBatchOperationV1',
    'ReviewBatchResultV1',
    'ReviewTaskPageV1',
    'ReviewTaskV1',
    'ContractErrorCode',
    'CrawlerJDEnvelopeV1',
    'DiscoveryJDSnapshotV2',
    'ensure_timezone_aware',
    'ExtractedJDBundleV1',
    'ExtractedJDBundleV2',
    'JDExtractionResult',
    'Evidence',
    'JDNormalizedResult',
    'RequirementGraph',
    'RequirementGraphChild',
    'RequirementGraphGroup',
    'CatalogSnapshotRefV1',
    'PositionCatalogSnapshotRefV1',
    'PublishedJDFactV3',
    'ValidationLineageV2',
    'build_published_jd_fact_v3',
    'ReleaseArtifactV1',
    'ReleaseManifestV1',
    'ReleaseMode',
    'SkillRelationSnapshotV1',
    'SkillRelationSnapshotV2',
    'normalize_source_record_id',
    'parse_crawl_time',
    'PositionReference',
    'StandardPositionRef',
    'StandardSkillRef',
    'StandardSkillSnapshotV1',
    'StandardSkillSnapshotV2',
    'CompetitionDemoManifestV1',
    'EXECUTION_MODE_SEMANTICS',
    'ExecutionMode',
    'ExecutionModeResultV1',
    'BusinessObjectRef',
    'EvidenceRAGCoverageV1',
    'EvidenceRAGQueryV1',
    'EvidenceRAGResponseV1',
    'PermissionContextV1',
    'RAGErrorV1',
    'RAGEvidenceReferenceV1',
    'RAGVersionScope',
    'parse_extracted_jd_bundle',
    'validate_bundle_matches_envelope',
    'validate_source_platform',
]
