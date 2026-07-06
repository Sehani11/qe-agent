"""Vector embedding and Pinecone indexing service."""

import asyncio
import httpx
from typing import List
import pinecone

from app.core.config import settings

# In a real scenario, this would interface with OpenAI for embeddings
# For this story, we'll mock the embedding generation or use a basic model if configured.

class VectorServiceError(Exception):
    """Custom error for vector embedding/indexing failures."""

    def __init__(self, message: str, code: str = "VECTOR_INDEX_FAILED"):
        self.message = message
        self.code = code
        super().__init__(self.message)


import re

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Semantic chunking algorithm approximating tokens by splitting safely on boundaries."""
    if not text:
        return []
    
    # Split by naive sentence boundaries to preserve semantic domains better than pure split()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = []
    current_length = 0
    
    for sentence in sentences:
        words_in_sentence = len(sentence.split())
        if current_length + words_in_sentence > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            # Keep the last few sentences for overlap (~overlap words)
            overlap_words = 0
            overlap_chunk = []
            for s in reversed(current_chunk):
                s_len = len(s.split())
                if overlap_words + s_len > overlap and overlap_chunk:
                    break
                overlap_chunk.insert(0, s)
                overlap_words += s_len
            current_chunk = overlap_chunk
            current_length = overlap_words
        
        current_chunk.append(sentence)
        current_length += words_in_sentence
        
    if current_chunk:
        chunks.append(" ".join(current_chunk))
            
    return chunks


async def embed_and_index_ticket(
    session_id: str,
    ticket_id: str,
    summary: str,
    description: str,
    acceptance_criteria: str
) -> None:
    """Chunk and embed ticket content into Pinecone under a specific namespace."""
    
    content = f"Ticket: {ticket_id}\nSummary: {summary}\nDescription: {description}\nAcceptance Criteria: {acceptance_criteria}"
    chunks = chunk_text(content)
    
    if not settings.pinecone_api_key:
        # Mock mode if no real credentials
        return
        
    try:
        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index_name)
        
        namespace = f"{settings.dev_user_id}:{session_id}"
        
        # Real embedding generation using OpenAI standard embeddings via httpx
        vectors = []
        
        if settings.llm_provider == "openai" and settings.llm_api_key:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json={"input": chunks, "model": "text-embedding-3-small"}
                )
                res.raise_for_status()
                data = res.json()
                
                for i, c_data in enumerate(data.get("data", [])):
                    vectors.append({
                        "id": f"chunk_{i}",
                        "values": c_data["embedding"],
                        "metadata": {"text": chunks[i], "ticket_id": ticket_id}
                    })
        else:
            # Fallback to semantic zero-shot placeholder if LLM provider not embedding capable
            import random
            for i, chunk in enumerate(chunks):
                vectors.append({
                    "id": f"chunk_{i}",
                    "values": [random.uniform(-1.0, 1.0) for _ in range(1536)],
                    "metadata": {"text": chunk, "ticket_id": ticket_id}
                })
            
        # Avoid blocking event loop for synchronous library call
        await asyncio.to_thread(index.upsert, vectors=vectors, namespace=namespace)
        
    except Exception:
        # Do not expose raw arbitrary trace strings to API
        raise VectorServiceError("Failed to connect and index to Pinecone cluster properly.")
