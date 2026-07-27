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

def custom_retry():
    """Return a tenacity retry decorator with exponential backoff.

    Retries on ``requests.RequestException``, ``NetworkError``, and
    ``RateLimitError`` up to ``llm.max_retries`` attempts.
    """
    return retry(
        stop=stop_after_attempt(config.get("llm.max_retries", 3)),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=(retry_if_exception_type((requests.RequestException, NetworkError, RateLimitError))),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )

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
        response = requests.post(url, timeout=timeout, **kwargs)
        if response.status_code == 429:
            raise RateLimitError(f"Rate limit exceeded: {response.text}")
        response.raise_for_status()
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
        response = requests.put(url, timeout=timeout, **kwargs)
        if response.status_code == 429:
            raise RateLimitError(f"Rate limit exceeded: {response.text}")
        response.raise_for_status()
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
            RateLimitError: On HTTP 429.
        """
        timeout = kwargs.pop('timeout', config.get("llm.timeout", 60))
        response = requests.get(url, timeout=timeout, **kwargs)
        if response.status_code == 429:
            raise RateLimitError(f"Rate limit exceeded: {response.text}")
        response.raise_for_status()
        return response
