"""Only reviewed adapters enter the registry; configuration cannot import code."""

from .anthropic_messages import ANTHROPIC_MESSAGES_ENDPOINT, AnthropicMessages
from .base import Provider
from .chat_completions import (
    OPENCODE_ZEN_CHAT_ENDPOINT,
    OPENROUTER_CHAT_ENDPOINT,
    XAI_CHAT_ENDPOINT,
    ChatCompletions,
)
from .gemini_generate import GEMINI_MODELS_ENDPOINT, GeminiGenerate
from .manual import Manual
from .ollama import Ollama
from .openai_responses import OPENAI_RESPONSES_ENDPOINT, OpenAIResponses

PROVIDERS: dict[str, Provider] = {
    "manual": Manual(),
    "ollama": Ollama(),
    "openai_responses": OpenAIResponses(),
    "anthropic_messages": AnthropicMessages(),
    "gemini_generate": GeminiGenerate(),
    # One reviewed Chat Completions adapter, locked per provider to its endpoint and output-limit field.
    "xai_chat": ChatCompletions("xai", XAI_CHAT_ENDPOINT, "max_tokens"),
    "openrouter_chat": ChatCompletions("openrouter", OPENROUTER_CHAT_ENDPOINT, "max_completion_tokens"),
    "opencode_zen_chat": ChatCompletions("opencode_zen", OPENCODE_ZEN_CHAT_ENDPOINT, "max_tokens"),
}
# The paid remote providers and the one reviewed origin each may call: they need a key reference, dated pricing and
# spend limits, and their usage is charged against the reservation.
REMOTE_ENDPOINTS: dict[str, str] = {
    "openai_responses": OPENAI_RESPONSES_ENDPOINT,
    "anthropic_messages": ANTHROPIC_MESSAGES_ENDPOINT,
    "gemini_generate": GEMINI_MODELS_ENDPOINT,
    "xai_chat": XAI_CHAT_ENDPOINT,
    "openrouter_chat": OPENROUTER_CHAT_ENDPOINT,
    "opencode_zen_chat": OPENCODE_ZEN_CHAT_ENDPOINT,
}
