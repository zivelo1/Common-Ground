"""Only reviewed adapters enter the registry; configuration cannot import code."""

from .base import Provider
from .manual import Manual
from .ollama import Ollama
from .openai_responses import OpenAIResponses

PROVIDERS: dict[str, Provider] = {
    "manual": Manual(),
    "ollama": Ollama(),
    "openai_responses": OpenAIResponses(),
}
