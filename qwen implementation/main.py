"""
main.py - Optimized Reddit Scraper with URL Logging and Rate Limiting
Includes:
- Token bucket rate limiting for PRAW API calls
- Exponential backoff for error handling
- URL logging to post_history.txt for successful posts
- Idempotent operations for Airflow-triggered tasks
"""
import requests
import pandas as pd
import datetime
import time
import os
import xml.etree.ElementTree as ET
import argparse
import random
import sys
import json
import subprocess
import tempfile
from urllib.parse import urlparse
from pathlib import Path

# --- CONFIGURATION ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

MIRRORS = [
    "https://old.reddit.com",
    "https://redlib.catsarch.com",
    "https://redlib.vsls.cz",
    "https://r.nf",
    "https://libreddit.northboot.xyz",
    "https://redlib.tux.pizza"
]

SEEN_URLS = set()
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# Post history log file path
POST_HISTORY_FILE = "qwen implementation/post_history.txt"


class TokenBucket:
    """Token bucket rate limiter for API calls."""
    
    def __init__(self, capacity: float = 60.0, refill_rate: float = 1.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.time()
    
    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now
    
    def acquire(self, timeout: float = None) -> bool:
        start_time = time.time()
        
        while True:
            self._refill()
            
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            
            if timeout is not None:
                if time.time() - start_time >= timeout:
                    return False
            
            time.sleep(0.1)
    
    def wait_and_acquire(self):
        """Wait until a token is available and acquire it."""
        while not self.acquire(timeout=0.5):
            pass
        return True


# Initialize rate limiters
praw_limiter = TokenBucket(capacity=60.0, refill_rate=1.0)  # 60 requests per minute


def log_post_url(url: str):
    """Log the URL of a successfully processed post to post_history.txt."""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(POST_HISTORY_FILE), exist_ok=True)
        
        timestamp = datetime.datetime.now().isoformat()
        log_entry = f"{timestamp} | {url}\n"
        
        with open(POST_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
        
        print(f"   📝 Logged URL to {POST_HISTORY_FILE}")
    except Exception as e:
        print(f"   ⚠️ Failed to log URL: {e}")


def setup_directories(target, prefix):
    """Creates organized folder structure for scraped data."""
    base_dir = f"data/{prefix}_{target}"
    dirs = {
        "base": base_dir,
        "posts": f"{base_dir}/posts.csv",
        "comments": f"{base_dir}/comments.csv",
        "media": f"{base_dir}/media",
        "images": f"{base_dir}/media/images",
        "videos": f"{base_dir}/media/videos",
    }
    
    for key in ["base", "media", "images", "videos"]:
        if not os.path.exists(dirs[key]):
            os.makedirs(dirs[key])
    
    return dirs


def get_file_path(target, type_prefix):
    """Legacy function for backward compatibility."""
    if not os.path.exists("data"):
        os.makedirs("data")
    sanitized_target = target.replace("/", "_")
    return f"data/{type_prefix}_{sanitized_target}.csv"


def load_history(filepath):
    """Loads existing CSV history to prevent duplicates (idempotent)."""
    SEEN_URLS.clear()
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath)
            for url in df['permalink']:
                SEEN_URLS.add(str(url))
            print(f"📚 Loaded {len(SEEN_URLS)} existing items from {filepath}")
        except:
            pass


def save_posts_csv(posts, filepath):
    """Saves posts to CSV with all metadata (idempotent)."""
    if not posts:
        return 0
    
    new_posts = [p for p in posts if p['permalink'] not in SEEN_URLS]
    
    if new_posts:
        df = pd.DataFrame(new_posts)
        if os.path.exists(filepath):
            df.to_csv(filepath, mode='a', header=False, index=False)
        else:
            df.to_csv(filepath, index=False)
        
        for p in new_posts:
            SEEN_URLS.add(p['permalink'])
            # Log URL to post_history.txt
            full_url = f"https://reddit.com{p['permalink']}"
            log_post_url(full_url)
        
        print(f"✅ Saved {len(new_posts)} new posts")
        return len(new_posts)
    else:
        print("💤 No new unique posts found.")
        return 0


def save_comments_csv(comments, filepath):
    """Saves comments to CSV (idempotent)."""
    if not comments:
        return
    
    df = pd.DataFrame(comments)
    if os.path.exists(filepath):
        df.to_csv(filepath, mode='a', header=False, index=False)
    else:
        df.to_csv(filepath, index=False)
    
    print(f"💬 Saved {len(comments)} comments")


# --- MEDIA DOWNLOAD ---
def get_media_urls(post_data):
    """Extracts all media URLs from a post."""
    media = {"images": [], "videos": [], "galleries": []}
    
    url = post_data.get('url', '')
    if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
        media["images"].append(url)
    
    if 'i.redd.it' in url:
        media["images"].append(url)
    
    if post_data.get('is_video'):
        reddit_video = post_data.get('media', {})
        if reddit_video and 'reddit_video' in reddit_video:
            video_url = reddit_video['reddit_video'].get('fallback_url', '')
            if video_url:
                media["videos"].append(video_url.split('?')[0])
    
    preview = post_data.get('preview', {})
    if preview and 'images' in preview:
        for img in preview['images']:
            source = img.get('source', {})
            if source.get('url'):
                clean_url = source['url'].replace('&amp;', '&')
                media["images"].append(clean_url)
    
    if post_data.get('is_gallery'):
        gallery_data = post_data.get('gallery_data', {})
        media_metadata = post_data.get('media_metadata', {})
        
        if gallery_data and media_metadata:
            for item in gallery_data.get('items', []):
                media_id = item.get('media_id')
                if media_id and media_id in media_metadata:
                    meta = media_metadata[media_id]
                    if meta.get('s', {}).get('u'):
                        clean_url = meta['s']['u'].replace('&amp;', '&')
                        media["galleries"].append(clean_url)
    
    if 'youtube.com' in url or 'youtu.be' in url:
        media["videos"].append(url)
    
    return media


def download_media(url, save_path, media_type="image"):
    """Downloads a single media file."""
    try:
        if os.path.exists(save_path):
            return True
        
        response = SESSION.get(url, timeout=30, stream=True)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception as e:
        pass
    return False


def scrape_comments(permalink, max_depth=3):
    """Scrapes comments from a post with rate limiting."""
    comments = []
    
    try:
        # Apply rate limiting
        praw_limiter.wait_and_acquire()
        
        if not permalink.startswith('http'):
            url = f"https://old.reddit.com{permalink}.json?limit=100"
        else:
            url = f"{permalink}.json?limit=100"
        
        response = SESSION.get(url, timeout=15)
        if response.status_code != 200:
            return comments
        
        data = response.json()
        
        if len(data) > 1:
            comment_data = data[1]['data']['children']
            comments = parse_comments(comment_data, permalink, depth=0, max_depth=max_depth)
    
    except Exception as e:
        print(f"   ⚠️ Error scraping comments: {e}")
    
    if len(comments) > 0:
        print(f"   + Scraped {len(comments)} comments")
    
    return comments


def parse_comments(comment_list, post_permalink, depth=0, max_depth=3):
    """Recursively parses comments."""
    comments = []
    
    if depth > max_depth:
        return comments
    
    for item in comment_list:
        if item['kind'] != 't1':
            continue
        
        c = item['data']
        
        comment = {
            "post_permalink": post_permalink,
            "comment_id": c.get('id'),
            "parent_id": c.get('parent_id'),
            "author": c.get('author'),
            "body": c.get('body', ''),
            "score": c.get('score', 0),
            "created_utc": datetime.datetime.fromtimestamp(c.get('created_utc', 0)).isoformat(),
            "depth": depth,
            "is_submitter": c.get('is_submitter', False),
        }
        comments.append(comment)
        
        replies = c.get('replies')
        if replies and isinstance(replies, dict):
            reply_children = replies.get('data', {}).get('children', [])
            comments.extend(parse_comments(reply_children, post_permalink, depth + 1, max_depth))
    
    return comments


def extract_post_data(post_json):
    """Extracts comprehensive post data."""
    p = post_json
    
    post_type = "text"
    if p.get('is_video'):
        post_type = "video"
    elif p.get('is_gallery'):
        post_type = "gallery"
    elif any(ext in p.get('url', '').lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']) or 'i.redd.it' in p.get('url', ''):
        post_type = "image"
    elif p.get('is_self'):
        post_type = "text"
    else:
        post_type = "link"
    
    return {
        "id": p.get('id'),
        "title": p.get('title'),
        "author": p.get('author'),
        "created_utc": datetime.datetime.fromtimestamp(p.get('created_utc', 0)).isoformat(),
        "permalink": p.get('permalink'),
        "url": p.get('url_overridden_by_dest', p.get('url')),
        "score": p.get('score', 0),
        "upvote_ratio": p.get('upvote_ratio', 0),
        "num_comments": p.get('num_comments', 0),
        "num_crossposts": p.get('num_crossposts', 0),
        "selftext": p.get('selftext', ''),
        "post_type": post_type,
        "is_nsfw": p.get('over_18', False),
        "is_spoiler": p.get('spoiler', False),
        "flair": p.get('link_flair_text', ''),
        "total_awards": p.get('total_awards_received', 0),
        "has_media": p.get('is_video', False) or p.get('is_gallery', False) or 'i.redd.it' in p.get('url', ''),
        "media_downloaded": False,
        "source": "History-Full"
    }


def run_full_history(target, limit, is_user=False, download_media_flag=True, 
                     scrape_comments_flag=True, dry_run=False, use_plugins=False):
    """
    Full scrape with images, videos, and comments.
    Implements token bucket rate limiting and exponential backoff.
    Logs all successful post URLs to post_history.txt.
    
    Args:
        target: Subreddit or username
        limit: Maximum posts to scrape
        is_user: True if target is a user
        download_media_flag: Download images/videos
        scrape_comments_flag: Scrape comments
        dry_run: Simulate without saving data
        use_plugins: Run post-processing plugins
    """
    prefix = "u" if is_user else "r"
    mode = "full" if download_media_flag and scrape_comments_flag else "history"
    
    # Display mode banner
    if dry_run:
        print("=" * 50)
        print("🧪 DRY RUN MODE - No data will be saved")
        print("=" * 50)
    
    print(f"🚀 Starting {'DRY RUN' if dry_run else 'FULL HISTORY'} scrape for {prefix}/{target}")
    print(f"   📊 Target posts: {limit}")
    print(f"   🖼️  Download media: {download_media_flag and not dry_run}")
    print(f"   💬 Scrape comments: {scrape_comments_flag}")
    print(f"   🔌 Plugins enabled: {use_plugins}")
    print("-" * 50)
    
    # Setup directories (even for dry run, to check existing data)
    dirs = setup_directories(target, prefix)
    load_history(dirs["posts"])
    
    after = None
    total_posts = 0
    total_media = {"images": 0, "videos": 0}
    total_comments = 0
    all_scraped_posts = []  # For plugin processing
    all_scraped_comments = []
    start_time = time.time()
    
    try:
        while total_posts < limit:
            random.shuffle(MIRRORS)
            success = False
            
            for base_url in MIRRORS:
                try:
                    # Apply rate limiting before each API call
                    praw_limiter.wait_and_acquire()
                    
                    if is_user:
                        path = f"/user/{target}/submitted.json"
                    else:
                        path = f"/r/{target}/new.json"
                    
                    params = {"limit": 100}
                    if after:
                        params["after"] = after
                    
                    url = base_url + path
                    response = SESSION.get(url, params=params, timeout=15)
                    
                    if response.status_code != 200:
                        continue
                    
                    data = response.json()
                    
                    if 'data' not in data or 'children' not in data['data']:
                        continue
                    
                    posts = []
                    batch_comments = []
                    
                    for child in data['data']['children']:
                        if total_posts >= limit:
                            break
                        
                        post_data = extract_post_data(child['data'])
                        
                        # Skip if already seen (idempotent)
                        if post_data['permalink'] in SEEN_URLS:
                            continue
                        
                        print(f"\n📄 Found: {post_data['title'][:50]}...")
                        
                        # Download media
                        if download_media_flag and post_data['has_media'] and not dry_run:
                            print(f"   🖼️  Downloading media...")
                            downloaded = download_post_media(post_data, dirs, post_data['id'])
                            total_media['images'] += downloaded['images']
                            total_media['videos'] += downloaded['videos']
                            post_data['media_downloaded'] = True
                        
                        posts.append(post_data)
                        
                        # Scrape comments with rate limiting
                        if scrape_comments_flag and post_data['num_comments'] > 0:
                            print(f"   💬 Fetching comments for: {post_data['title'][:40]}...")
                            comments = scrape_comments(post_data['permalink'])
                            batch_comments.extend(comments)
                            total_comments += len(comments)
                    
                    # Collect for plugins
                    all_scraped_posts.extend(posts)
                    all_scraped_comments.extend(batch_comments)
                    
                    # Save data (skip in dry run)
                    if not dry_run:
                        saved = save_posts_csv(posts, dirs["posts"])
                        total_posts += saved
                        
                        if batch_comments:
                            save_comments_csv(batch_comments, dirs["comments"])
                    else:
                        # In dry run, just count
                        total_posts += len(posts)
                        print(f"   🧪 [DRY RUN] Would save {len(posts)} posts")
                    
                    print(f"\n📊 Progress: {total_posts}/{limit} posts")
                    print(f"   🖼️  Images: {total_media['images']} | 🎬 Videos: {total_media['videos']}")
                    print(f"   💬 Comments: {total_comments}")
                    
                    after = data['data'].get('after')
                    if not after:
                        print("\n🏁 Reached end of available history.")
                        break
                    
                    success = True
                    break
                    
                except Exception as e:
                    print(f"   ⚠️ Error with {base_url}: {e}")
                    # Exponential backoff on error
                    time.sleep(2 ** (MIRRORS.index(base_url)))
                    continue
            
            if not after:
                break
                
            if not success:
                print("\n❌ All sources failed. Waiting 30s...")
                time.sleep(30)
            else:
                print(f"\n⏸️ Cooling down (3s)...")
                time.sleep(3)
        
        # Run plugins on collected data
        if use_plugins and (all_scraped_posts or all_scraped_comments):
            print("\n🔌 Running post-processing plugins...")
            try:
                from plugins import load_plugins, run_plugins
                plugins = load_plugins()
                if plugins:
                    all_scraped_posts, all_scraped_comments = run_plugins(
                        all_scraped_posts, all_scraped_comments
                    )
                    print(f"   ✅ Processed by {len(plugins)} plugins")
            except Exception as e:
                print(f"   ⚠️ Plugin error: {e}")
        
        # Complete job tracking
        elapsed = time.time() - start_time
        print("\n" + "=" * 50)
        print(f"✅ COMPLETED in {elapsed:.1f}s")
        print(f"   📊 Total posts: {total_posts}")
        print(f"   🖼️  Images downloaded: {total_media['images']}")
        print(f"   🎬 Videos downloaded: {total_media['videos']}")
        print(f"   💬 Comments scraped: {total_comments}")
        print(f"   📝 URLs logged to: {POST_HISTORY_FILE}")
        print("=" * 50)
        
        # Update job record
        try:
            from export.database import complete_job_record
            complete_job_record(job_id, total_posts, total_comments, 
                              total_media['images'] + total_media['videos'])
        except Exception as e:
            print(f"⚠️ Job tracking unavailable: {e}")
        
        return {
            "success": True,
            "posts": total_posts,
            "comments": total_comments,
            "media": total_media,
            "elapsed": elapsed
        }
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
        return {"success": False, "error": "Interrupted"}
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        return {"success": False, "error": str(e)}


def download_post_media(post_data, dirs, post_id):
    """Downloads all media from a post."""
    media = get_media_urls(post_data)
    downloaded = {"images": 0, "videos": 0}
    
    for i, img_url in enumerate(media["images"][:5]):
        ext = os.path.splitext(urlparse(img_url).path)[1] or '.jpg'
        save_path = os.path.join(dirs["images"], f"{post_id}_{i}{ext}")
        if download_media(img_url, save_path, "image"):
            downloaded["images"] += 1
    
    for i, img_url in enumerate(media["galleries"][:10]):
        ext = '.jpg'
        save_path = os.path.join(dirs["images"], f"{post_id}_gallery_{i}{ext}")
        if download_media(img_url, save_path, "gallery"):
            downloaded["images"] += 1
    
    for i, vid_url in enumerate(media["videos"][:2]):
        if 'youtube' not in vid_url:
            ext = '.mp4'
            save_path = os.path.join(dirs["videos"], f"{post_id}_{i}{ext}")
            if download_media(vid_url, save_path, "video"):
                downloaded["videos"] += 1
    
    return downloaded


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reddit Full History Scraper")
    parser.add_argument("target", help="Subreddit or username to scrape")
    parser.add_argument("-l", "--limit", type=int, default=100, help="Max posts to scrape")
    parser.add_argument("-u", "--user", action="store_true", help="Target is a user")
    parser.add_argument("--no-media", action="store_true", help="Skip media download")
    parser.add_argument("--no-comments", action="store_true", help="Skip comments")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without saving")
    parser.add_argument("--plugins", action="store_true", help="Run post-processing plugins")
    
    args = parser.parse_args()
    
    result = run_full_history(
        target=args.target,
        limit=args.limit,
        is_user=args.user,
        download_media_flag=not args.no_media,
        scrape_comments_flag=not args.no_comments,
        dry_run=args.dry_run,
        use_plugins=args.plugins
    )
    
    sys.exit(0 if result.get("success") else 1)
