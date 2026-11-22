"""Tests for embeddings service."""

import pytest
from openai import RateLimitError

from app.services.embeddings import (
    EmbeddingsError,
    EmbeddingsRateLimitError,
    OpenAIEmbeddingsProvider,
    get_embeddings_provider,
)


class TestOpenAIEmbeddingsProvider:
    """Tests for OpenAI embeddings provider."""

    @pytest.fixture
    def provider(self) -> OpenAIEmbeddingsProvider:
        """Create provider instance."""
        # Skip if no API key configured
        try:
            return OpenAIEmbeddingsProvider()
        except ValueError:
            pytest.skip("OpenAI API key not configured")

    @pytest.mark.asyncio
    async def test_get_embedding_success(self, provider: OpenAIEmbeddingsProvider):
        """Test single embedding generation."""
        text = "This is a test message about AI and machine learning."

        embedding = await provider.get_embedding(text)

        assert isinstance(embedding, list)
        assert len(embedding) == 768  # text-embedding-3-small dimensions
        assert all(isinstance(v, float) for v in embedding)

    @pytest.mark.asyncio
    async def test_get_embedding_empty_text(self, provider: OpenAIEmbeddingsProvider):
        """Test embedding generation fails for empty text."""
        with pytest.raises(EmbeddingsError, match="Cannot embed empty text"):
            await provider.get_embedding("")

        with pytest.raises(EmbeddingsError, match="Cannot embed empty text"):
            await provider.get_embedding("   ")

    @pytest.mark.asyncio
    async def test_get_embedding_caching(self, provider: OpenAIEmbeddingsProvider):
        """Test that embeddings are cached."""
        text = "Test message for caching"

        # First call generates embedding
        embedding1 = await provider.get_embedding(text)

        # Second call should use cache (same result)
        embedding2 = await provider.get_embedding(text)

        assert embedding1 == embedding2

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_success(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test batch embedding generation."""
        texts = [
            "First message about technology",
            "Second message about AI",
            "Third message about machine learning",
        ]

        embeddings = await provider.get_embeddings_batch(texts)

        assert len(embeddings) == 3
        assert all(len(emb) == 768 for emb in embeddings)
        assert all(isinstance(v, float) for emb in embeddings for v in emb)

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_empty_list(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test batch embedding with empty list."""
        embeddings = await provider.get_embeddings_batch([])
        assert embeddings == []

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_filters_empty(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test batch embedding filters out empty texts."""
        texts = ["Valid text", "", "  ", "Another valid text"]

        # Should not raise, only process valid texts
        embeddings = await provider.get_embeddings_batch(texts)
        assert len(embeddings) == 2

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_too_large(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test batch size limit."""
        texts = [f"Message {i}" for i in range(101)]  # Exceeds max 100

        with pytest.raises(EmbeddingsError, match="exceeds max"):
            await provider.get_embeddings_batch(texts)

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_all_empty(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test batch with all empty texts."""
        texts = ["", "  ", "\n\t"]

        with pytest.raises(EmbeddingsError, match="No valid texts"):
            await provider.get_embeddings_batch(texts)

    @pytest.mark.asyncio
    async def test_get_embedding_multilingual(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test embedding generation for Russian text."""
        russian_text = "Это тестовое сообщение на русском языке о машинном обучении"

        embedding = await provider.get_embedding(russian_text)

        assert isinstance(embedding, list)
        assert len(embedding) == 768


class TestEmbeddingsProviderFactory:
    """Tests for provider factory."""

    def test_get_openai_provider(self):
        """Test getting OpenAI provider."""
        try:
            provider = get_embeddings_provider("openai")
            assert isinstance(provider, OpenAIEmbeddingsProvider)
        except ValueError:
            pytest.skip("OpenAI API key not configured")

    def test_get_ollama_provider_not_implemented(self):
        """Test Ollama provider raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            get_embeddings_provider("ollama")

    def test_get_unknown_provider(self):
        """Test unknown provider raises ValueError."""
        with pytest.raises(ValueError, match="Unknown embeddings provider"):
            get_embeddings_provider("unknown")


class TestEmbeddingsSimilarity:
    """Tests for semantic similarity using embeddings."""

    @pytest.fixture
    def provider(self) -> OpenAIEmbeddingsProvider:
        """Create provider instance."""
        try:
            return OpenAIEmbeddingsProvider()
        except ValueError:
            pytest.skip("OpenAI API key not configured")

    @pytest.mark.asyncio
    async def test_similar_texts_have_high_similarity(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test that semantically similar texts have high cosine similarity."""
        text1 = "OpenAI releases GPT-5 model"
        text2 = "GPT-5 launched by OpenAI today"

        emb1 = await provider.get_embedding(text1)
        emb2 = await provider.get_embedding(text2)

        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        magnitude1 = sum(a * a for a in emb1) ** 0.5
        magnitude2 = sum(b * b for b in emb2) ** 0.5
        similarity = dot_product / (magnitude1 * magnitude2)

        # Similar texts should have similarity > 0.85
        assert similarity > 0.85, f"Expected similarity >0.85, got {similarity}"

    @pytest.mark.asyncio
    async def test_dissimilar_texts_have_low_similarity(
        self, provider: OpenAIEmbeddingsProvider
    ):
        """Test that unrelated texts have lower similarity."""
        text1 = "OpenAI releases GPT-5 model"
        text2 = "Recipe for chocolate cake with vanilla frosting"

        emb1 = await provider.get_embedding(text1)
        emb2 = await provider.get_embedding(text2)

        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        magnitude1 = sum(a * a for a in emb1) ** 0.5
        magnitude2 = sum(b * b for b in emb2) ** 0.5
        similarity = dot_product / (magnitude1 * magnitude2)

        # Dissimilar texts should have similarity < 0.5
        assert similarity < 0.5, f"Expected similarity <0.5, got {similarity}"
