import math


class EmbeddingRuleViolation(ValueError):
    pass


def cosine_similarity(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / (denominator or 1.0)
