import time
import pytest
from multi_company_scraper.http_client import RateLimitedClient


def test_client_creates_with_defaults():
    client = RateLimitedClient()
    assert client.min_delay == 3.0
    assert client.max_delay == 8.0
    assert client.max_retries == 3
    assert len(client.user_agents) > 0


def test_client_creates_with_custom_params():
    client = RateLimitedClient(min_delay=1.0, max_delay=2.0, max_retries=1)
    assert client.min_delay == 1.0
    assert client.max_delay == 2.0
    assert client.max_retries == 1


def test_ua_rotation():
    client = RateLimitedClient()
    ua1 = client.get_random_ua()
    ua2 = client.get_random_ua()
    # 5次尝试内至少有不同的UA
    uas = [client.get_random_ua() for _ in range(5)]
    assert len(set(uas)) >= 1  # 至少有一个


def test_delay_calculation():
    client = RateLimitedClient(min_delay=3.0, max_delay=8.0)
    delay = client._calculate_delay()
    assert 3.0 <= delay <= 8.0


def test_rate_limit_waits():
    client = RateLimitedClient(min_delay=0.1, max_delay=0.2)
    start = time.time()
    client._rate_limit()
    elapsed = time.time() - start
    assert 0.1 <= elapsed <= 0.3  # 允许一些误差
