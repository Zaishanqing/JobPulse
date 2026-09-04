from fastapi import Request

from app.contexts.discovery import (
    DiscoveryCandidateHandlers,
    PositionDiscoveryHandlers,
)
from app.contexts.emerging_positions import EmergingPositionHandlers
from app.contexts.jd_lifecycle import JDUseCases
from app.contexts.platform import ManageOutboxEvents
from app.api.dependencies.container import get_application_container


def get_jd_use_cases(request: Request) -> JDUseCases:
    return get_application_container(request).jds


def get_position_discovery_handlers(request: Request) -> PositionDiscoveryHandlers:
    return get_application_container(request).discovery


def get_discovery_candidate_handlers(request: Request) -> DiscoveryCandidateHandlers:
    return get_application_container(request).discovery_candidates


def get_emerging_position_handlers(request: Request) -> EmergingPositionHandlers:
    return get_application_container(request).emerging_positions


def get_outbox_event_use_cases(request: Request) -> ManageOutboxEvents:
    return get_application_container(request).outbox_events
