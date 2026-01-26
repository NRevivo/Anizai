import time
import uuid

# ==========================================
# 1. The Core Envelope
# ==========================================
def create_unified_message(
    source: str,
    stream_type: str,
    text_content: str,
    url: str,
    timestamp_str: str,
    metadata: dict = None
) -> dict:
    """
    Standardized envelope for ALL data sources.
    Adheres to the project's unified schema requirements.
    
    Args:
        source: The origin of data (e.g., 'news_api', 'reddit', 'arxiv').
        stream_type: The broad category (e.g., 'news', 'community', 'professional').
        text_content: The main text payload (title + body).
        url: Link to the original content.
        timestamp_str: Creation time from the source.
        metadata: Source-specific raw fields.
    """
    if metadata is None:
        metadata = {}

    return {
        # --- Standard Fields (Jira Requirement Compliant) ---
        "event_id": str(uuid.uuid4()),    # Renamed from message_id
        "stream_type": stream_type,       # NEW: Required for Spark routing
        "source": source,                 # Renamed from source_type
        
        "text": text_content,             # Maps to 'raw_text/title' requirement
        "url": url,
        "timestamp": timestamp_str,       # Renamed from created_at
        
        # --- System Fields ---
        "ingested_at": time.time(),       # Internal processing timestamp
        
        # --- Context / Raw Data ---
        "metadata": metadata              # Preserves original source specifics
    }

# ==========================================
# 2. Source Mappers
# ==========================================

# --- Source 1: NewsAPI ---
def map_news_to_unified(article: dict, category: str) -> dict:
    """
    Maps NewsAPI data to the 'news_stream'.
    """
    return create_unified_message(
        source="news_api",
        stream_type="news_stream",  # Routing tag
        text_content=f"{article.get('title', '')}. {article.get('description') or ''}",
        url=article.get('url', ''),
        timestamp_str=article.get('publishedAt', ''),
        metadata={
            "source_name": article.get('source', {}).get('name'),
            "author": article.get('author'),
            "category": category
        }
    )

# --- Source 2: Reddit ---
def map_reddit_to_unified(submission: dict) -> dict:
    """
    Maps Reddit data to the 'community_stream'.
    """
    full_text = f"{submission.get('title', '')}\n{submission.get('selftext', '')}"
    return create_unified_message(
        source="reddit",
        stream_type="community_stream", # Routing tag
        text_content=full_text,
        url=submission.get('url', ''),
        timestamp_str=str(submission.get('created_utc', '')),
        metadata={
            "subreddit": submission.get('subreddit_name_prefixed'),
            "score": submission.get('score', 0),
            "num_comments": submission.get('num_comments', 0),
            "author": str(submission.get('author', 'unknown'))
        }
    )

# --- Source 3: ArXiv ---
def map_arxiv_to_unified(result: dict) -> dict:
    """
    Maps ArXiv data to the 'professional_stream'.
    """
    return create_unified_message(
        source="arxiv",
        stream_type="professional_stream", # Routing tag
        text_content=f"{result.get('title', '')}\nSummary: {result.get('summary', '')}",
        url=result.get('id', ''),
        timestamp_str=str(result.get('published', '')),
        metadata={
            "categories": result.get('categories', []),
            "primary_category": result.get('primary_category', ''),
            "authors": [a.name for a in result.get('authors', [])]
        }
    )

# --- Source 4: Hacker News ---
def map_hacker_news_to_unified(story: dict) -> dict:
    """
    Maps Hacker News data to the 'community_stream'.
    """
    content = story.get('text', '') 
    full_text = f"{story.get('title', '')}\n{content}"

    return create_unified_message(
        source="hacker_news",
        stream_type="community_stream", # Routing tag
        text_content=full_text,
        url=story.get('url', ''),
        timestamp_str=str(story.get('time', '')),
        metadata={
            "score": story.get('score', 0),
            "descendants": story.get('descendants', 0), # comment count
            "by": story.get('by', ''),
            "type": story.get('type', 'story')
        }
    )

# --- Source 5: YouTube ---
def map_youtube_to_unified(video_item: dict) -> dict:
    """
    Maps YouTube data to the 'community_stream'.
    """
    snippet = video_item.get('snippet', {})
    video_id = video_item.get('id', {}).get('videoId')
    if not video_id and isinstance(video_item.get('id'), str):
         video_id = video_item.get('id')

    return create_unified_message(
        source="youtube",
        stream_type="community_stream", # Routing tag
        text_content=f"{snippet.get('title', '')}\n{snippet.get('description', '')}",
        url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        timestamp_str=snippet.get('publishedAt', ''),
        metadata={
            "channel_title": snippet.get('channelTitle'),
            "channel_id": snippet.get('channelId'),
            "video_id": video_id,
            "tags": snippet.get('tags', [])
        }
    )