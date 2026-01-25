import time
import uuid

# ==========================================
# 1. The Core Envelope (המעטפה הראשית)
# ==========================================
def create_unified_message(
    source_type: str,
    text_content: str,
    url: str,
    created_at_str: str,
    metadata: dict = None
) -> dict:
    """
    Standardized envelope for ALL data sources.
    This structure ensures Spark can process everything uniformly.
    """
    if metadata is None:
        metadata = {}

    return {
        # --- Standard Fields (Used by Spark & OpenAI) ---
        "message_id": str(uuid.uuid4()),   # Unique ID for tracing
        "source_type": source_type,        # 'news', 'reddit', 'arxiv', 'hacker_news', 'youtube'
        
        "text": text_content,              # The main content for embedding
        "url": url,
        "created_at": created_at_str,
        "ingested_at": time.time(),
        
        # --- Source Specifics (Context for the AI) ---
        "metadata": metadata               # Source-specific raw fields
    }

# ==========================================
# 2. Source Mappers (פונקציות המרה לכל מקור)
# ==========================================

# --- Source 1: NewsAPI ---
def map_news_to_unified(article: dict, category: str) -> dict:
    return create_unified_message(
        source_type="news",
        text_content=f"{article.get('title', '')}. {article.get('description') or ''}",
        url=article.get('url', ''),
        created_at_str=article.get('publishedAt', ''),
        metadata={
            "source_name": article.get('source', {}).get('name'),
            "author": article.get('author'),
            "category": category
        }
    )

# --- Source 2: Reddit ---
def map_reddit_to_unified(submission: dict) -> dict:
    """
    Maps a PRAW Reddit submission object (dict) to unified format.
    """
    full_text = f"{submission.get('title', '')}\n{submission.get('selftext', '')}"
    return create_unified_message(
        source_type="reddit",
        text_content=full_text,
        url=submission.get('url', ''),
        created_at_str=str(submission.get('created_utc', '')),
        metadata={
            "subreddit": submission.get('subreddit_name_prefixed'),
            "score": submission.get('score', 0),
            "num_comments": submission.get('num_comments', 0),
            "author": str(submission.get('author', 'unknown'))
        }
    )

# --- Source 3: ArXiv ---
def map_arxiv_to_unified(result: dict) -> dict:
    return create_unified_message(
        source_type="arxiv",
        text_content=f"{result.get('title', '')}\nSummary: {result.get('summary', '')}",
        url=result.get('id', ''),
        created_at_str=str(result.get('published', '')),
        metadata={
            "categories": result.get('categories', []),
            "primary_category": result.get('primary_category', ''),
            "authors": [a.name for a in result.get('authors', [])]
        }
    )

# --- Source 4: Hacker News (החדש!) ---
def map_hacker_news_to_unified(story: dict) -> dict:
    """
    Maps a Hacker News item to unified format.
    HN usually provides a title and a link, rarely text unless it's an 'Ask HN'.
    """
    # אם יש טקסט (כמו ב-Ask HN) נשתמש בו, אחרת רק הכותרת
    content = story.get('text', '') # עשוי להכיל HTML, ננקה בהמשך אם צריך
    full_text = f"{story.get('title', '')}\n{content}"

    return create_unified_message(
        source_type="hacker_news",
        text_content=full_text,
        url=story.get('url', ''), # HN items often point to external URLs
        created_at_str=str(story.get('time', '')), # Unix timestamp
        metadata={
            "score": story.get('score', 0),
            "descendants": story.get('descendants', 0), # מספר התגובות
            "by": story.get('by', ''), # שם המשתמש
            "type": story.get('type', 'story') # story, job, poll
        }
    )

# --- Source 5: YouTube (החדש!) ---
def map_youtube_to_unified(video_item: dict) -> dict:
    """
    Maps a YouTube API search result or video details to unified format.
    """
    snippet = video_item.get('snippet', {})
    video_id = video_item.get('id', {}).get('videoId')
    if not video_id and isinstance(video_item.get('id'), str):
         video_id = video_item.get('id') # לעיתים ה-ID מגיע ישירות כמחרוזת

    return create_unified_message(
        source_type="youtube",
        text_content=f"{snippet.get('title', '')}\n{snippet.get('description', '')}",
        url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        created_at_str=snippet.get('publishedAt', ''),
        metadata={
            "channel_title": snippet.get('channelTitle'),
            "channel_id": snippet.get('channelId'),
            "video_id": video_id,
            "tags": snippet.get('tags', []) # רשימת תגיות אם קיימת
        }
    )