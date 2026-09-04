def test_deprecated_services_facade_only_forwards_official_owners():
    from app import services
    from app.domain.policies import align_extraction, align_quote, normalize_key
    from app.infrastructure.providers.normalization import Normalizer, normalize_salary
    from app.infrastructure.sqlalchemy.fact_mappers import (
        persist_extracted,
        persist_normalized,
    )

    assert services.align_extraction is align_extraction
    assert services.align_quote is align_quote
    assert services.normalize_key is normalize_key
    assert services.Normalizer is Normalizer
    assert services.normalize_salary is normalize_salary
    assert services.persist_extracted is persist_extracted
    assert services.persist_normalized is persist_normalized
