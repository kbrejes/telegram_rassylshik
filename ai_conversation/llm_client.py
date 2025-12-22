"""
Unified LLM Client

Provides a single interface for multiple LLM backends:
- Ollama (local)
- LM Studio (local)
- OpenAI (cloud)

All backends use OpenAI-compatible API, so we use the same client with different base_url.
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from openai import OpenAI, AsyncOpenAI

logger = logging.getLogger(__name__)


@dataclass
class LLMProviderConfig:
    """Configuration for an LLM provider."""
    name: str
    base_url: str
    api_key: str
    default_model: str

    @classmethod
    def ollama(cls, model: str = "qwen2.5:3b") -> "LLMProviderConfig":
        """Create Ollama provider config."""
        return cls(
            name="ollama",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            default_model=model,
        )

    @classmethod
    def lm_studio(cls, model: str = "qwen2.5-vl-7b-instruct") -> "LLMProviderConfig":
        """Create LM Studio provider config."""
        return cls(
            name="lm_studio",
            base_url="http://127.0.0.1:1234/v1",
            api_key="lm-studio",
            default_model=model,
        )

    @classmethod
    def openai(cls, model: str = "gpt-4o-mini", api_key: Optional[str] = None) -> "LLMProviderConfig":
        """Create OpenAI provider config."""
        return cls(
            name="openai",
            base_url="https://api.openai.com/v1",
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            default_model=model,
        )

    @classmethod
    def groq(cls, model: str = "llama-3.3-70b-versatile", api_key: Optional[str] = None) -> "LLMProviderConfig":
        """Create Groq provider config."""
        return cls(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key or os.getenv("GROQ_API_KEY", ""),
            default_model=model,
        )

    @classmethod
    def together(cls, model: str = "meta-llama/Llama-3.2-3B-Instruct-Turbo", api_key: Optional[str] = None) -> "LLMProviderConfig":
        """Create Together AI provider config (has free tier)."""
        return cls(
            name="together",
            base_url="https://api.together.xyz/v1",
            api_key=api_key or os.getenv("TOGETHER_API_KEY", ""),
            default_model=model,
        )

    @classmethod
    def openrouter(cls, model: str = "meta-llama/llama-3.2-3b-instruct:free", api_key: Optional[str] = None) -> "LLMProviderConfig":
        """Create OpenRouter provider config (has free models with :free suffix)."""
        return cls(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key or os.getenv("OPENROUTER_API_KEY", ""),
            default_model=model,
        )

    @classmethod
    def gemini(cls, model: str = "gemini-2.0-flash", api_key: Optional[str] = None) -> "LLMProviderConfig":
        """Create Google Gemini provider config (15 RPM, 1M tokens/month free)."""
        return cls(
            name="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=api_key or os.getenv("GEMINI_API_KEY", ""),
            default_model=model,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMProviderConfig":
        """Create from dictionary (for config file parsing)."""
        api_key = data.get("api_key", "")
        # Expand environment variables
        if api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            api_key = os.getenv(env_var, "")

        return cls(
            name=data.get("name", "custom"),
            base_url=data.get("base_url", ""),
            api_key=api_key,
            default_model=data.get("default_model", ""),
        )


class UnifiedLLMClient:
    """
    Unified LLM client that works with any OpenAI-compatible API.

    Usage:
        # Using preset
        client = UnifiedLLMClient.from_provider("ollama")

        # Using custom config
        config = LLMProviderConfig(...)
        client = UnifiedLLMClient(config)

        # Chat
        response = client.chat([
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"}
        ])

        # Async chat
        response = await client.achat([...])
    """

    # Preset providers
    PROVIDERS = {
        "ollama": LLMProviderConfig.ollama,
        "lm_studio": LLMProviderConfig.lm_studio,
        "openai": LLMProviderConfig.openai,
        "groq": LLMProviderConfig.groq,
        "together": LLMProviderConfig.together,
        "openrouter": LLMProviderConfig.openrouter,
        "gemini": LLMProviderConfig.gemini,
    }

    # Fallback providers config: (provider_factory, models_to_try)
    # Priority: big/quality models first, small models as last resort
    FALLBACK_CHAIN = [
        # Groq big models
        ("groq", [
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-120b",      # 120B open-source GPT
            "qwen/qwen3-32b",           # 32B Qwen
        ]),
        # Google Gemini (15 RPM, 1M tok/month free)
        ("gemini", ["gemini-2.0-flash", "gemini-1.5-flash"]),
        # OpenRouter free models
        ("openrouter", [
            "google/gemini-2.0-flash-exp:free",
            "qwen/qwen3-coder:free",
        ]),
        # Groq smaller models as last resort
        ("groq", [
            "meta-llama/llama-4-scout-17b-16e-instruct",  # Llama 4 17B
            "llama-3.1-8b-instant",
        ]),
    ]

    def __init__(
        self,
        config: LLMProviderConfig,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        """
        Initialize the LLM client.

        Args:
            config: Provider configuration
            model: Model to use (overrides config default)
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
        """
        self.config = config
        self.model = model or config.default_model
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Sync client
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

        # Async client
        self._aclient = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

        logger.info(f"[LLM] Initialized {config.name} client with model {self.model}")

    @classmethod
    def from_provider(
        cls,
        provider: str,
        model: Optional[str] = None,
        **kwargs
    ) -> "UnifiedLLMClient":
        """
        Create client from provider name.

        Args:
            provider: Provider name ("ollama", "lm_studio", "openai")
            model: Model to use (optional)
            **kwargs: Additional arguments for UnifiedLLMClient
        """
        if provider not in cls.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}. Available: {list(cls.PROVIDERS.keys())}")

        config_factory = cls.PROVIDERS[provider]
        config = config_factory(model) if model else config_factory()

        return cls(config, model=model, **kwargs)

    @classmethod
    def from_config(
        cls,
        providers_config: Dict[str, Any],
        provider_name: str,
        model: Optional[str] = None,
        **kwargs
    ) -> "UnifiedLLMClient":
        """
        Create client from config dictionary.

        Args:
            providers_config: Dictionary with provider configurations
            provider_name: Name of provider to use
            model: Model to use (optional)
            **kwargs: Additional arguments
        """
        if provider_name not in providers_config:
            # Try preset
            return cls.from_provider(provider_name, model=model, **kwargs)

        provider_data = providers_config[provider_name]
        provider_data["name"] = provider_name
        config = LLMProviderConfig.from_dict(provider_data)

        return cls(config, model=model, **kwargs)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Synchronous chat completion.

        Args:
            messages: List of messages [{"role": "...", "content": "..."}]
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            Response text
        """
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
            )

            content = response.choices[0].message.content
            logger.debug(f"[LLM] Response: {content[:100]}...")
            return content

        except Exception as e:
            logger.error(f"[LLM] Chat error: {e}")
            raise

    async def achat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Asynchronous chat completion with multi-provider fallback.

        Fallback order (big models first):
        1. Groq llama-3.3-70b, llama-3.1-70b
        2. Together AI llama-3.1-70b
        3. OpenRouter llama-3.1-70b:free, qwen-2.5-72b:free
        4. Groq llama-3.1-8b (last resort)

        Args:
            messages: List of messages [{"role": "...", "content": "..."}]
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            Response text
        """
        # Build list of (client, model, provider_name) to try
        attempts = []
        tried_providers = set()

        for provider_name, models in self.FALLBACK_CHAIN:
            provider_factory = self.PROVIDERS.get(provider_name)
            if not provider_factory:
                continue

            config = provider_factory()
            if not config.api_key:
                continue  # Skip if no API key

            # Create client for this provider
            if provider_name == self.config.name:
                client = self._aclient
            else:
                client = AsyncOpenAI(
                    base_url=config.base_url,
                    api_key=config.api_key,
                )

            for model in models:
                attempts.append((client, model, provider_name))

        last_error = None
        for client, model, provider in attempts:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature or self.temperature,
                    max_tokens=max_tokens or self.max_tokens,
                )

                content = response.choices[0].message.content
                if model != self.model or provider != self.config.name:
                    logger.info(f"[LLM] Used fallback: {provider}/{model}")
                logger.debug(f"[LLM] Response: {content[:100]}...")
                return content

            except Exception as e:
                error_str = str(e).lower()
                # Rate limit, quota, auth errors, or model issues - try next
                if any(x in str(e) for x in ["429", "401", "403", "400"]) or \
                   any(x in error_str for x in ["rate_limit", "quota", "unauthorized", "invalid_api_key",
                                                  "decommissioned", "not_found", "does not exist"]):
                    logger.warning(f"[LLM] {provider}/{model} failed: {str(e)[:80]}, trying next...")
                    last_error = e
                    continue
                else:
                    logger.error(f"[LLM] Error on {provider}/{model}: {e}")
                    raise

        logger.error(f"[LLM] All providers exhausted")
        raise last_error or Exception("No LLM providers available")

    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for text.

        Note: Not all providers support embeddings.
        For Ollama, use a model like "nomic-embed-text".
        For OpenAI, use "text-embedding-3-small".

        Args:
            text: Text to embed

        Returns:
            Embedding vector
        """
        try:
            response = self._client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding

        except Exception as e:
            logger.error(f"[LLM] Embedding error: {e}")
            raise

    async def aembed(self, text: str) -> List[float]:
        """Async version of embed."""
        try:
            response = await self._aclient.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding

        except Exception as e:
            logger.error(f"[LLM] Async embedding error: {e}")
            raise

    def is_available(self) -> bool:
        """Check if the LLM service is available."""
        try:
            # Simple test request
            self._client.models.list()
            return True
        except Exception:
            return False

    async def ais_available(self) -> bool:
        """Async check if the LLM service is available."""
        try:
            await self._aclient.models.list()
            return True
        except Exception:
            return False

    def __repr__(self) -> str:
        return f"UnifiedLLMClient(provider={self.config.name}, model={self.model})"
