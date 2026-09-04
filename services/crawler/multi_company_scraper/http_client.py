import random
import time
from typing import Optional
import requests
from loguru import logger


class RateLimitedClient:
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]

    def __init__(
        self,
        min_delay: float = 3.0,
        max_delay: float = 8.0,
        max_retries: int = 3,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.user_agents = self.USER_AGENTS
        self.session = requests.Session()
        self._last_request_time: float = 0

    def get_random_ua(self) -> str:
        return random.choice(self.USER_AGENTS)

    def _calculate_delay(self) -> float:
        return random.uniform(self.min_delay, self.max_delay)

    def _rate_limit(self):
        delay = self._calculate_delay()
        if self._last_request_time == 0:
            # First request: wait the full delay
            time.sleep(delay)
        else:
            elapsed = time.time() - self._last_request_time
            if elapsed < delay:
                time.sleep(delay - elapsed)
        self._last_request_time = time.time()

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("headers", {})
        kwargs["headers"]["User-Agent"] = self.get_random_ua()
        kwargs.setdefault("timeout", 30)

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._rate_limit()
                resp = self.session.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last_error = e
                logger.warning(
                    f"Request failed (attempt {attempt}/{self.max_retries}): {url} - {e}"
                )
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)

        raise last_error  # type: ignore

    def get(self, url: str, **kwargs) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._request("POST", url, **kwargs)
