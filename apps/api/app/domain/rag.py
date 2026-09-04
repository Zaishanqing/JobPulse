import re


def lexical_tokens(value: str) -> set[str]:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())
    latin = set(re.findall(r"[a-z0-9]{2,}", value.lower()))
    cjk = {normalized[index : index + 2] for index in range(len(normalized) - 1)}
    return latin | cjk


def validate_claims(claims: list[str], evidence_text: str, has_evidence: bool) -> tuple[list[str], list[str]]:
    evidence_tokens = lexical_tokens(evidence_text)
    supported, unsupported = [], []
    for claim in claims:
        claim_tokens = lexical_tokens(claim)
        overlap = len(claim_tokens & evidence_tokens) / max(len(claim_tokens), 1)
        (supported if has_evidence and overlap >= 0.2 else unsupported).append(claim)
    return supported, unsupported
