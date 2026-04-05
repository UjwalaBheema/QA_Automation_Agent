import os
from anthropic import Anthropic
from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def ask(
    system_prompt: str,
    user_message: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
) -> str:
    client = get_client()
    logger.debug(f"Calling Claude [{model}] | prompt length: {len(user_message)} chars")
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    text = response.content[0].text
    logger.debug(f"Claude response length: {len(text)} chars")
    return text
