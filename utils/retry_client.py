from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import requests
import logging
from config.config import config
from utils.logger import logger

class NetworkError(Exception):
    """Raised when a network request fails after retries."""
    pass

class RateLimitError(Exception):
    """Raised when the API returns HTTP 429."""
    pass

class ServerError(Exception):
    """Raised on transient upstream HTTP 5xx (502/503/504) so the retry
    decorator can back off and try again."""
    pass

class LLMConnectionError(Exception):
    """Raised when the LLM API endpoint cannot be reached.

    Provides a human-readable diagnosis of the connection failure.
    """
    def __init__(self, url: str, original_error: Exception):
        self.url = url
        self.original_error = original_error
        # Extract host from URL for a cleaner message
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or url
        except Exception:
            host = url

        msg = (
            f"无法连接到 LLM 服务 ({host})。\n"
            f"  原始错误: {original_error}\n"
            f"  请检查:\n"
            f"    1) 网络连接是否正常\n"
            f"    2) API 地址 ({host}) 是否正确\n"
            f"    3) 该服务是否正在运行\n"
            f"    4) 是否需要代理/VPN 才能访问"
        )
        super().__init__(msg)


def _diagnose_connection_error(error: Exception, url: str) -> str:
    """Translate a requests ConnectionError into a human-readable diagnosis."""
    err_str = str(error)

    if "Max retries exceeded" in err_str:
        if "refused" in err_str.lower() or "10061" in err_str:
            return f"连接被拒绝 — 服务可能未启动或地址错误: {url}"
        if "timed out" in err_str.lower():
            return f"连接超时 — 网络不稳定或服务响应过慢: {url}"
        if "could not resolve" in err_str.lower() or "getaddrinfo" in err_str.lower():
            return f"DNS 解析失败 — 域名不存在或网络不通: {url}"
        return f"连接失败 (多次重试后): {url}"

    return f"网络错误: {error}"


def custom_retry():
    """Return a tenacity retry decorator with exponential backoff.

    Retries on ``requests.ConnectionError``, ``NetworkError``,
    ``RateLimitError``, and ``ServerError`` (transient 502/503/504) up to
    ``llm.max_retries`` attempts.

    Note: Other HTTP errors (4xx, non-transient 5xx) are raised immediately
    since retrying won't fix a bad request or server config issue.
    """
    max_attempts = config.get("llm.max_retries", 3)
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=(
            retry_if_exception_type(
                (requests.ConnectionError, NetworkError, RateLimitError, ServerError)
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


_TRANSIENT_STATUS = (502, 503, 504)


def _raise_for_status(response):
    """429 → RateLimitError, transient 5xx → ServerError, else raise_for_status."""
    if response.status_code == 429:
        raise RateLimitError(f"Rate limit exceeded: {response.text}")
    if response.status_code in _TRANSIENT_STATUS:
        raise ServerError(f"HTTP {response.status_code}: {response.text[:200]}")
    response.raise_for_status()

class RetrySession:
    """Static wrapper around ``requests`` with automatic retries and rate-limit handling."""

    @staticmethod
    @custom_retry()
    def post(url, **kwargs):
        """Send a POST request with retry logic.

        Args:
            url: Target URL.
            **kwargs: Passed to ``requests.post``; ``timeout`` defaults to
                ``llm.timeout`` config value.

        Returns:
            ``requests.Response`` on success.

        Raises:
            RateLimitError: On HTTP 429.
        """
        timeout = kwargs.pop('timeout', config.get("llm.timeout", 60))
        try:
            response = requests.post(url, timeout=timeout, **kwargs)
        except requests.ConnectionError as e:
            raise NetworkError(_diagnose_connection_error(e, url)) from e
        _raise_for_status(response)
        return response

    @staticmethod
    @custom_retry()
    def put(url, **kwargs):
        """Send a PUT request with retry logic (used for file uploads).

        Args:
            url: Target URL.
            **kwargs: Passed to ``requests.put``; ``timeout`` defaults to 300s.

        Returns:
            ``requests.Response`` on success.

        Raises:
            RateLimitError: On HTTP 429.
        """
        timeout = kwargs.pop('timeout', config.get("llm.timeout", 300))
        try:
            response = requests.put(url, timeout=timeout, **kwargs)
        except requests.ConnectionError as e:
            raise NetworkError(_diagnose_connection_error(e, url)) from e
        _raise_for_status(response)
        return response

    @staticmethod
    @custom_retry()
    def get(url, **kwargs):
        """Send a GET request with retry logic.

        Args:
            url: Target URL.
            **kwargs: Passed to ``requests.get``; ``timeout`` defaults to
                ``llm.timeout`` config value.

        Returns:
            ``requests.Response`` on success.

        Raises:
            NetworkError: On connection failure with human-readable diagnosis.
            RateLimitError: On HTTP 429.
        """
        timeout = kwargs.pop('timeout', config.get("llm.timeout", 60))
        try:
            response = requests.get(url, timeout=timeout, **kwargs)
        except requests.ConnectionError as e:
            raise NetworkError(_diagnose_connection_error(e, url)) from e
        _raise_for_status(response)
        return response
