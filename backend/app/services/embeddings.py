"""Embeddings service with provider abstraction for future Ollama support."""

from typing import Protocol

import structlog
from openai import AsyncOpenAI, OpenAIError

from app.core.config import settings

log = structlog.get_logger()


class EmbeddingsProvider(Protocol):
    """Protocol for embeddings providers (OpenAI, Ollama, etc.)."""

    async def get_embedding(self, text: str) -> list[float]:
        """Generate embedding vector for text.

        Args:
            text: Input text to embed

        Returns:
            768-dimensional embedding vector

        Raises:
            EmbeddingsError: If embedding generation fails
        """
        ...

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in batch.

        Args:
            texts: List of texts to embed (max 100)

        Returns:
            List of 768-dimensional embedding vectors

        Raises:
            EmbeddingsError: If embedding generation fails
        """
        ...


class EmbeddingsError(Exception):
    """Base exception for embeddings operations."""

    pass


class EmbeddingsRateLimitError(EmbeddingsError):
    """Raised when API rate limit is hit."""

    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class OpenAIEmbeddingsProvider:
    """OpenAI embeddings provider implementation."""

    MODEL = "text-embedding-3-small"  # 768 dimensions
    MAX_BATCH_SIZE = 100
    DIMENSIONS = 768

    def __init__(self, api_key: str | None = None):
        """Initialize OpenAI client.

        Args:
            api_key: OpenAI API key (defaults to settings.OPENAI_API_KEY)
        """
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")

        self.client = AsyncOpenAI(api_key=self.api_key)
        self._embedding_cache: dict[str, list[float]] = {}

    async def get_embedding(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Input text to embed

        Returns:
            768-dimensional embedding vector

        Raises:
            EmbeddingsError: If generation fails
        """
        if not text or not text.strip():
            raise EmbeddingsError("Cannot embed empty text")

        # Check cache
        cache_key = text[:200]  # Use first 200 chars as cache key
        if cache_key in self._embedding_cache:
            await log.adebug("Embedding cache hit", text_preview=text[:50])
            return self._embedding_cache[cache_key]

        try:
            await log.ainfo(
                "Generating embedding",
                model=self.MODEL,
                text_length=len(text),
            )

            response = await self.client.embeddings.create(
                input=text,
                model=self.MODEL,
                dimensions=self.DIMENSIONS,
            )

            embedding = response.data[0].embedding

            # Cache result
            self._embedding_cache[cache_key] = embedding

            await log.ainfo(
                "Embedding generated",
                vector_dim=len(embedding),
                usage_tokens=response.usage.total_tokens,
            )

            return embedding

        except OpenAIError as e:
            if "rate_limit" in str(e).lower():
                await log.aerror("OpenAI rate limit hit", error=str(e))
                raise EmbeddingsRateLimitError() from e

            await log.aerror("OpenAI embedding failed", error=str(e))
            raise EmbeddingsError(f"OpenAI API error: {e}") from e

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in batch.

        Args:
            texts: List of texts to embed (max 100)

        Returns:
            List of embedding vectors

        Raises:
            EmbeddingsError: If generation fails
        """
        if not texts:
            return []

        if len(texts) > self.MAX_BATCH_SIZE:
            raise EmbeddingsError(
                f"Batch size {len(texts)} exceeds max {self.MAX_BATCH_SIZE}"
            )

        # Filter empty texts
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            raise EmbeddingsError("No valid texts to embed")

        try:
            await log.ainfo(
                "Generating batch embeddings",
                model=self.MODEL,
                batch_size=len(valid_texts),
            )

            response = await self.client.embeddings.create(
                input=valid_texts,
                model=self.MODEL,
                dimensions=self.DIMENSIONS,
            )

            embeddings = [item.embedding for item in response.data]

            # Cache results
            for text, embedding in zip(valid_texts, embeddings, strict=True):
                cache_key = text[:200]
                self._embedding_cache[cache_key] = embedding

            await log.ainfo(
                "Batch embeddings generated",
                count=len(embeddings),
                usage_tokens=response.usage.total_tokens,
            )

            return embeddings

        except OpenAIError as e:
            if "rate_limit" in str(e).lower():
                await log.aerror("OpenAI rate limit hit in batch", error=str(e))
                raise EmbeddingsRateLimitError() from e

            await log.aerror("OpenAI batch embedding failed", error=str(e))
            raise EmbeddingsError(f"OpenAI API error: {e}") from e


# Future Ollama provider stub
class OllamaEmbeddingsProvider:
    """Ollama embeddings provider (future implementation)."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        """Initialize Ollama client.

        Args:
            base_url: Ollama server URL
        """
        self.base_url = base_url
        raise NotImplementedError("Ollama provider not yet implemented")

    async def get_embedding(self, text: str) -> list[float]:
        """Generate embedding using Ollama."""
        raise NotImplementedError("Ollama provider not yet implemented")

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate batch embeddings using Ollama."""
        raise NotImplementedError("Ollama provider not yet implemented")


# Provider factory
def get_embeddings_provider(provider: str = "openai") -> EmbeddingsProvider:
    """Get embeddings provider instance.

    Args:
        provider: Provider name ('openai' or 'ollama')

    Returns:
        Embeddings provider instance

    Raises:
        ValueError: If provider is unknown or not configured
    """
    if provider == "openai":
        return OpenAIEmbeddingsProvider()
    elif provider == "ollama":
        return OllamaEmbeddingsProvider()
    else:
        raise ValueError(f"Unknown embeddings provider: {provider}")


# Global service instance
embeddings_service = get_embeddings_provider(
    provider=getattr(settings, "EMBEDDINGS_PROVIDER", "openai")
)
