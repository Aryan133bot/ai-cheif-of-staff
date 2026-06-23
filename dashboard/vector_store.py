import os
import chromadb
from pathlib import Path

# Setup Chroma DB persistent client
DB_DIR = Path(__file__).resolve().parent / "chroma_db"
DB_DIR.mkdir(exist_ok=True)
client = chromadb.PersistentClient(path=str(DB_DIR))

# Use sentence-transformers default embedding model
collection = client.get_or_create_collection(
    name="knowledge_base",
    metadata={"hnsw:space": "cosine"}
)

def add_to_collection(user_id: int, entry_id: int, title: str, content: str):
    """Upserts a knowledge base entry into ChromaDB."""
    doc_id = f"{user_id}_{entry_id}"
    text = f"Title: {title}\nContent: {content}"
    collection.upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[{"user_id": user_id, "entry_id": entry_id, "title": title}]
    )

def query_collection(user_id: int, query: str, n_results: int = 5) -> list[str]:
    """Queries the ChromaDB for relevant facts for a specific user."""
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where={"user_id": user_id}
    )
    
    # Return the list of document strings
    if results["documents"] and results["documents"][0]:
        return results["documents"][0]
    return []

def delete_from_collection(user_id: int, entry_id: int):
    """Deletes an entry from ChromaDB."""
    doc_id = f"{user_id}_{entry_id}"
    collection.delete(ids=[doc_id])

def delete_all_for_user(user_id: int):
    """Deletes all entries for a specific user."""
    collection.delete(where={"user_id": user_id})
