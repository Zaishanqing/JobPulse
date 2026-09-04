from __future__ import annotations

# ── unified permission constants ─────────────────────────────────────────────

ACCOUNT_MANAGE = 'account.manage'
CATALOG_READ_PUBLISHED = 'catalog.read_published'
EMERGING_READ_PUBLISHED = 'emerging.read_published'
EVIDENCE_READ_PUBLIC = 'evidence.read_public'
KG_BUILD_MANAGE = 'kg.build.manage'
KG_NORMALIZATION_MANAGE = 'kg.normalization.manage'
KG_REVIEW_MANAGE = 'kg.review.manage'
KG_VERSION_MANAGE = 'kg.version.manage'
EMERGING_DISCOVERY_MANAGE = 'emerging.discovery.manage'
EMERGING_CANDIDATE_MANAGE = 'emerging.candidate.manage'
EMERGING_PUBLISH_MANAGE = 'emerging.publish.manage'
CATALOG_PROMOTE_MANAGE = 'catalog.promote.manage'
INTEGRATION_STATUS_VIEW = 'integration.status.view'
INTEGRATION_CV_RETRY = 'integration.cv.retry'
INTEGRATION_JD_RETRY = 'integration.jd.retry'
INTEGRATION_OUTBOX_REQUEUE = 'integration.outbox.requeue'
INTEGRATION_WORKER_RUN = 'integration.worker.run'
ACQUISITION_READ = 'acquisition.read'
ACQUISITION_JOB_MANAGE = 'acquisition.job.manage'
JD_CREATE = 'jd.create'
JD_PARSE = 'jd.parse'
JD_PUBLISH = 'jd.publish'
RESUME_PARSE_MANAGE = 'resume.parse.manage'
RESUME_PROFILE_GENERATE = 'resume.profile.generate'
MATCHING_RUN = 'matching.run'
LEARNING_PATH_CREATE = 'learning_path.create'
TREND_RUN_MANAGE = 'trend.run.manage'
TREND_SOURCE_MANAGE = 'trend.source.manage'
TREND_REVIEW_MANAGE = 'trend.review.manage'
TREND_PUBLISH_MANAGE = 'trend.publish.manage'
TREND_PUBLISHED_READ = 'trend.published.read'

_ALL_PERMISSIONS = frozenset({
    ACCOUNT_MANAGE,
    CATALOG_READ_PUBLISHED,
    EMERGING_READ_PUBLISHED,
    EVIDENCE_READ_PUBLIC,
    KG_BUILD_MANAGE,
    KG_NORMALIZATION_MANAGE,
    KG_REVIEW_MANAGE,
    KG_VERSION_MANAGE,
    EMERGING_DISCOVERY_MANAGE,
    EMERGING_CANDIDATE_MANAGE,
    EMERGING_PUBLISH_MANAGE,
    CATALOG_PROMOTE_MANAGE,
    INTEGRATION_STATUS_VIEW,
    INTEGRATION_CV_RETRY,
    INTEGRATION_JD_RETRY,
    INTEGRATION_OUTBOX_REQUEUE,
    INTEGRATION_WORKER_RUN,
    ACQUISITION_READ,
    ACQUISITION_JOB_MANAGE,
    JD_CREATE,
    JD_PARSE,
    JD_PUBLISH,
    RESUME_PARSE_MANAGE,
    RESUME_PROFILE_GENERATE,
    MATCHING_RUN,
    LEARNING_PATH_CREATE,
    TREND_RUN_MANAGE,
    TREND_SOURCE_MANAGE,
    TREND_REVIEW_MANAGE,
    TREND_PUBLISH_MANAGE,
    TREND_PUBLISHED_READ,
})

_PUBLIC_READ_PERMISSIONS = frozenset({
    CATALOG_READ_PUBLISHED,
    EMERGING_READ_PUBLISHED,
    EVIDENCE_READ_PUBLIC,
    TREND_PUBLISHED_READ,
})

# ── single authoritative role → permission mapping ──────────────────────────

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    'personal_user': _PUBLIC_READ_PERMISSIONS | frozenset({
        RESUME_PARSE_MANAGE,
        RESUME_PROFILE_GENERATE,
        MATCHING_RUN,
        LEARNING_PATH_CREATE,
    }),
    'enterprise_user': _PUBLIC_READ_PERMISSIONS | frozenset({
        JD_CREATE,
        JD_PARSE,
    }),
    'reviewer': _PUBLIC_READ_PERMISSIONS | frozenset({
        KG_NORMALIZATION_MANAGE,
        KG_REVIEW_MANAGE,
        INTEGRATION_STATUS_VIEW,
        ACQUISITION_READ,
        TREND_REVIEW_MANAGE,
    }),
    'admin': _ALL_PERMISSIONS,
    'developer': _PUBLIC_READ_PERMISSIONS | frozenset({
        INTEGRATION_STATUS_VIEW,
        INTEGRATION_CV_RETRY,
        INTEGRATION_JD_RETRY,
        INTEGRATION_OUTBOX_REQUEUE,
        INTEGRATION_WORKER_RUN,
        ACQUISITION_READ,
        ACQUISITION_JOB_MANAGE,
        JD_CREATE,
        JD_PARSE,
        JD_PUBLISH,
        RESUME_PARSE_MANAGE,
        RESUME_PROFILE_GENERATE,
        MATCHING_RUN,
        LEARNING_PATH_CREATE,
        TREND_RUN_MANAGE,
        TREND_SOURCE_MANAGE,
        TREND_REVIEW_MANAGE,
        TREND_PUBLISH_MANAGE,
    }),
}


ALL_PERMISSIONS = _ALL_PERMISSIONS


def permissions_for_role(role: str) -> tuple[str, ...]:
    """Return a stable, sorted tuple of permissions for *role*.

    Unknown roles receive an empty permission set — no implicit public permissions.
    """
    return tuple(sorted(ROLE_PERMISSIONS.get(role, frozenset())))


def require_permission(role: str, permission: str) -> None:
    if permission not in permissions_for_role(role):
        from app.domain.errors import PermissionDenied

        raise PermissionDenied(f'Missing permission: {permission}')
