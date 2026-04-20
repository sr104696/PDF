"""
rag_manager.py - RAG Manager with Unified SQLite Database Integration
Performs incremental updates, only processing rows that haven't been indexed.
"""
import os
import pickle
import numpy as np
from sentence_transformers import SentenceTransformer
import pandas as pd
from typing import List, Tuple, Optional, Set
import hashlib
from datetime import datetime
import logging
import sqlite3
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RAGManager:
    """Manages embeddings with persistence and incremental updates using unified SQLite database."""
    
    def __init__(
        self, 
        model_name: str = 'all-MiniLM-L6-v2', 
        embeddings_dir: str = 'embeddings',
        db_path: str = 'reddit_leads.db'
    ):
        self.model_name = model_name
        self.embeddings_dir = embeddings_dir
        self.embeddings_file = os.path.join(embeddings_dir, 'embeddings.pkl')
        self.metadata_file = os.path.join(embeddings_dir, 'metadata.pkl')
        self.db_path = db_path
        
        # Create embeddings directory if it doesn't exist
        os.makedirs(embeddings_dir, exist_ok=True)
        
        # Initialize model
        logger.info(f"Loading SentenceTransformer model: {model_name}")
        self.model = SentenceTransformer(model_name)
        
        # Load existing embeddings and metadata
        self.embeddings = None
        self.metadata = None
        self.df = None
        self._load_embeddings()
    
    def _get_db_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_indexed_ids_from_db(self) -> Set[str]:
        """Get set of already indexed post/comment IDs from the database."""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Check if embedding_indexed column exists in posts table
            cursor.execute("PRAGMA table_info(posts)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'embedding_indexed' in columns:
                # Get posts with embeddings already computed
                cursor.execute("""
                    SELECT id FROM posts WHERE embedding_indexed = 1
                """)
                indexed_ids = {row['id'] for row in cursor.fetchall()}
            else:
                # Fallback: check if embedding BLOB is not NULL
                cursor.execute("""
                    SELECT id FROM posts WHERE embedding IS NOT NULL
                """)
                indexed_ids = {row['id'] for row in cursor.fetchall()}
            
            conn.close()
            return indexed_ids
        except Exception as e:
            logger.error(f"Error getting indexed IDs from database: {e}")
            return set()
    
    def _mark_as_indexed(self, post_ids: List[str]):
        """Mark posts as indexed in the database."""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Check if embedding_indexed column exists
            cursor.execute("PRAGMA table_info(posts)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'embedding_indexed' in columns:
                cursor.executemany("""
                    UPDATE posts SET embedding_indexed = 1, embedding_indexed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, [(pid,) for pid in post_ids])
                conn.commit()
            else:
                logger.warning("embedding_indexed column not found. Consider running migration.")
            
            conn.close()
            logger.info(f"Marked {len(post_ids)} posts as indexed in database")
        except Exception as e:
            logger.error(f"Error marking posts as indexed: {e}")
    
    def _load_embeddings(self):
        """Load existing embeddings and metadata from disk."""
        try:
            if os.path.exists(self.embeddings_file) and os.path.exists(self.metadata_file):
                logger.info("Loading existing embeddings from disk...")
                with open(self.embeddings_file, 'rb') as f:
                    self.embeddings = pickle.load(f)
                with open(self.metadata_file, 'rb') as f:
                    self.metadata = pickle.load(f)
                logger.info(f"Loaded {len(self.embeddings)} embeddings")
            else:
                logger.info("No existing embeddings found")
                self.embeddings = np.array([])
                self.metadata = {
                    'indexed_ids': set(),
                    'last_update': None,
                    'data_hash': None
                }
        except Exception as e:
            logger.error(f"Error loading embeddings: {e}")
            self.embeddings = np.array([])
            self.metadata = {
                'indexed_ids': set(),
                'last_update': None,
                'data_hash': None
            }
    
    def _save_embeddings(self):
        """Save embeddings and metadata to disk."""
        try:
            logger.info("Saving embeddings to disk...")
            with open(self.embeddings_file, 'wb') as f:
                pickle.dump(self.embeddings, f)
            with open(self.metadata_file, 'wb') as f:
                pickle.dump(self.metadata, f)
            logger.info("Embeddings saved successfully")
        except Exception as e:
            logger.error(f"Error saving embeddings: {e}")
    
    def _get_data_hash(self, df: pd.DataFrame) -> str:
        """Generate a hash of the dataframe to detect changes."""
        # Use shape and sample of data to create hash
        hash_str = f"{df.shape}_{df.head(10).to_string()}_{df.tail(10).to_string()}"
        return hashlib.md5(hash_str.encode()).hexdigest()
    
    def fetch_unindexed_data(self, limit: int = 1000) -> pd.DataFrame:
        """Fetch unindexed data from the unified SQLite database.
        
        Only retrieves posts/comments that haven't been embedded yet.
        This ensures incremental updates are efficient.
        """
        try:
            conn = self._get_db_connection()
            
            # Check if embedding_indexed column exists
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(posts)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'embedding_indexed' in columns:
                # Fetch only unindexed posts
                query = """
                    SELECT id, title, selftext, subreddit, author, url, created_utc
                    FROM posts
                    WHERE embedding_indexed = 0 OR embedding_indexed IS NULL
                    ORDER BY created_utc DESC
                    LIMIT ?
                """
            else:
                # Fallback: fetch posts where embedding is NULL
                query = """
                    SELECT id, title, selftext, subreddit, author, url, created_utc
                    FROM posts
                    WHERE embedding IS NULL
                    ORDER BY created_utc DESC
                    LIMIT ?
                """
            
            df = pd.read_sql_query(query, conn, params=(limit,))
            conn.close()
            
            if df.empty:
                logger.info("No unindexed data found in database")
                return pd.DataFrame()
            
            # Prepare text for embedding (combine title and selftext)
            df['text'] = df['title'].fillna('') + ' ' + df['selftext'].fillna('')
            df['text'] = df['text'].str.strip()
            
            # Filter out empty texts
            df = df[df['text'].notna() & (df['text'] != '')]
            
            logger.info(f"Fetched {len(df)} unindexed posts from database")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching unindexed data: {e}")
            return pd.DataFrame()
    
    def update_embeddings(self, df: Optional[pd.DataFrame] = None, force_refresh: bool = False):
        """Update embeddings for new or changed data.
        
        If df is None, fetches unindexed data from the unified database.
        Only processes rows that haven't been indexed yet (incremental updates).
        """
        # Fetch from database if no dataframe provided
        if df is None:
            df = self.fetch_unindexed_data()
        
        if df is None or df.empty:
            logger.warning("No data to index")
            return
        
        self.df = df
        current_hash = self._get_data_hash(df)
        
        # Check if we need to refresh all embeddings
        if force_refresh or current_hash != self.metadata.get('data_hash'):
            logger.info("Data has changed or force refresh requested. Rebuilding all embeddings...")
            self._build_all_embeddings()
        else:
            # Check for new entries (incremental update)
            new_ids = set(df['id'].values) - self.metadata.get('indexed_ids', set())
            if new_ids:
                logger.info(f"Found {len(new_ids)} new entries to index")
                self._build_incremental_embeddings(new_ids)
                # Mark as indexed in database
                self._mark_as_indexed(list(new_ids))
            else:
                logger.info("No new entries to index")
    
    def _build_all_embeddings(self):
        """Build embeddings for all entries."""
        if self.df is None or self.df.empty:
            return
        
        # Filter out entries with empty text
        valid_df = self.df[self.df['text'].notna() & (self.df['text'] != '')]
        
        if valid_df.empty:
            logger.warning("No valid text entries to encode")
            return
        
        logger.info(f"Encoding {len(valid_df)} texts...")
        texts = valid_df['text'].tolist()
        
        # Encode in batches for memory efficiency
        batch_size = 32
        all_embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = self.model.encode(batch, show_progress_bar=True)
            all_embeddings.append(batch_embeddings)
        
        self.embeddings = np.vstack(all_embeddings)
        self.metadata = {
            'indexed_ids': set(valid_df['id'].values),
            'last_update': datetime.now(),
            'data_hash': self._get_data_hash(self.df),
            'valid_indices': valid_df.index.tolist()
        }
        
        self._save_embeddings()
        logger.info(f"Built and saved {len(self.embeddings)} embeddings")
    
    def _build_incremental_embeddings(self, new_ids: set):
        """Build embeddings only for new entries."""
        if self.df is None:
            return
        
        # Get new entries
        new_df = self.df[self.df['id'].isin(new_ids)]
        valid_new_df = new_df[new_df['text'].notna() & (new_df['text'] != '')]
        
        if valid_new_df.empty:
            logger.warning("No valid new entries to encode")
            return
        
        logger.info(f"Encoding {len(valid_new_df)} new texts...")
        new_texts = valid_new_df['text'].tolist()
        new_embeddings = self.model.encode(new_texts, show_progress_bar=True)
        
        # Append to existing embeddings
        if self.embeddings.size > 0:
            self.embeddings = np.vstack([self.embeddings, new_embeddings])
        else:
            self.embeddings = new_embeddings
        
        # Update metadata
        if 'indexed_ids' not in self.metadata:
            self.metadata['indexed_ids'] = set()
        self.metadata['indexed_ids'].update(valid_new_df['id'].values)
        self.metadata['last_update'] = datetime.now()
        self.metadata['data_hash'] = self._get_data_hash(self.df)
        
        # Update valid indices
        if 'valid_indices' in self.metadata:
            self.metadata['valid_indices'].extend(valid_new_df.index.tolist())
        else:
            self.metadata['valid_indices'] = valid_new_df.index.tolist()
        
        self._save_embeddings()
        logger.info(f"Added {len(new_embeddings)} new embeddings")
    
    def find_similar_documents(self, query: str, top_k: int = 5) -> List[str]:
        """Find similar documents using cosine similarity."""
        if self.embeddings is None or self.embeddings.size == 0:
            logger.warning("No embeddings available")
            return ["No documents indexed yet. Please run the scraper first."]
        
        if self.df is None:
            logger.error("No dataframe loaded")
            return ["Error: No data available"]
        
        # Encode query
        query_embedding = self.model.encode([query])
        
        # Calculate cosine similarity
        cosine_scores = np.dot(self.embeddings, query_embedding.T).flatten()
        
        # Get top k indices
        top_k_indices = np.argsort(cosine_scores)[-top_k:][::-1]
        
        # Map back to original dataframe indices
        valid_indices = self.metadata.get('valid_indices', [])
        results = []
        
        for idx in top_k_indices:
            if idx < len(valid_indices):
                df_idx = valid_indices[idx]
                if df_idx < len(self.df):
                    row = self.df.iloc[df_idx]
                    text = row['text']
                    score = cosine_scores[idx]
                    author = row.get('author', 'unknown')
                    post_type = row.get('subreddit', 'unknown')
                    
                    # Add more context for better analysis
                    if score > 0.7:  # High relevance
                        results.append(f"[HIGH RELEVANCE - Score: {score:.3f}] {post_type.upper()} by u/{author}: {text}")
                    elif score > 0.5:  # Medium relevance
                        results.append(f"[Score: {score:.3f}] {post_type} by u/{author}: {text}")
                    else:  # Lower relevance
                        results.append(f"[Score: {score:.3f}] {text}")
        
        return results if results else ["No similar documents found"]
    
    def get_stats(self) -> dict:
        """Get statistics about the indexed data."""
        return {
            'total_embeddings': len(self.embeddings) if self.embeddings is not None else 0,
            'last_update': str(self.metadata.get('last_update', 'Never')),
            'indexed_ids': len(self.metadata.get('indexed_ids', set())),
            'model': self.model_name
        }


def initialize_rag_system(db_path: str = 'reddit_leads.db'):
    """Initialize the RAG system with incremental indexing from the unified database."""
    rag_manager = RAGManager(db_path=db_path)
    
    logger.info("Initializing RAG system with incremental updates...")
    
    # Update embeddings - will only process unindexed rows
    rag_manager.update_embeddings()
    
    stats = rag_manager.get_stats()
    logger.info(f"RAG system initialized. Stats: {stats}")
    
    return rag_manager


if __name__ == "__main__":
    # Example usage
    rag = initialize_rag_system()
    print(rag.get_stats())
