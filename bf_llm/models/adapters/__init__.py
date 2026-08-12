from . import base
from . import anthropic
from . import openai


def make_adapter(resolved):
    """Return the adapter instance for a resolved provider.

    OpenAI and openai_compatible/local share the same wire format, so both are
    served by the OpenAI adapter.
    """
    provider_type = resolved.provider.provider
    if provider_type == "anthropic":
        return anthropic.AnthropicAdapter(resolved)
    return openai.OpenAIAdapter(resolved)
