from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import requests
import logging
from config.config import config
from utils.logger import logger

class NetworkError(Exception):
    pass

class RateLimitError(Exception):
    pass

def custom_retry():
    """Decorator for standard retry logic with tenacity"""
    return retry(
        stop=stop_after_attempt(config.get("llm.max_retries", 3)),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=(retry_if_exception_type((requests.RequestException, NetworkError, RateLimitError))),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )

class RetrySession:
    @staticmethod
    @custom_retry()
    def post(url, **kwargs):
        timeout = kwargs.pop('timeout', config.get("llm.timeout", 60))
        response = requests.post(url, timeout=timeout, **kwargs)
        if response.status_code == 429:
            raise RateLimitError(f"Rate limit exceeded: {response.text}")
        response.raise_for_status()
        return response

    @staticmethod
    @custom_retry()
    def get(url, **kwargs):
        timeout = kwargs.pop('timeout', config.get("llm.timeout", 60))
        response = requests.get(url, timeout=timeout, **kwargs)
        if response.status_code == 429:
            raise RateLimitError(f"Rate limit exceeded: {response.text}")
        response.raise_for_status()
        return response
