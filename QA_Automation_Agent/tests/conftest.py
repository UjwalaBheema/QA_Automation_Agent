import asyncio
import os
import pytest
from dotenv import load_dotenv

load_dotenv(override=True)


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def chartrequest_url():
    return os.getenv("CHARTREQUEST_URL", "https://qa.chartrequest.com")


@pytest.fixture(scope="session")
def credentials():
    return {
        "email": os.getenv("CHARTREQUEST_EMAIL", ""),
        "password": os.getenv("CHARTREQUEST_PASSWORD", ""),
    }
