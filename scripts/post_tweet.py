"""
Zenn 新規記事公開時に X (Twitter) へ自動投稿するスクリプト。
GitHub Actions から呼び出される。

動作:
  - git diff で articles/ 配下の変更ファイルを検出
  - frontmatter の published が false → true になった記事のみツイート
  - 新規ファイルで published: true の場合もツイート
"""

import os
import re
import subprocess
import sys

import tweepy
import yaml


ZENN_USERNAME = os.environ.get("ZENN_USERNAME", "swallow_eng")


def get_frontmatter(content: str) -> dict:
    """Markdown の frontmatter を辞書で返す。"""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if match:
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}
    return {}


def get_changed_article_paths() -> list[str]:
    """直前のコミットと比較して変更された articles/*.md を返す。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        capture_output=True,
        text=True,
    )
    files = result.stdout.strip().split("\n")
    return [f for f in files if f.startswith("articles/") and f.endswith(".md")]


def was_just_published(filepath: str) -> bool:
    """
    このコミットで published: true になった記事かどうかを判定。
    - 新規ファイルで published: true → True
    - 既存ファイルで false → true に変わった → True
    - それ以外 → False
    """
    if not os.path.exists(filepath):
        return False

    with open(filepath, encoding="utf-8") as f:
        current_content = f.read()
    current_fm = get_frontmatter(current_content)

    if not current_fm.get("published", False):
        return False

    # 前のコミットのファイル内容を取得
    prev_result = subprocess.run(
        ["git", "show", f"HEAD~1:{filepath}"],
        capture_output=True,
        text=True,
    )
    if prev_result.returncode != 0:
        # 新規ファイル → published: true ならツイート対象
        return True

    prev_fm = get_frontmatter(prev_result.stdout)
    # 以前は false で今回 true になった場合のみ対象
    return not prev_fm.get("published", False)


def build_tweet(fm: dict, slug: str) -> str:
    """frontmatter から投稿文を生成する（280文字以内）。"""
    title = fm.get("title", "新しい記事を公開しました")
    topics = fm.get("topics", [])
    url = f"https://zenn.dev/{ZENN_USERNAME}/articles/{slug}"

    # ハッシュタグは最大5個
    hashtags = " ".join(f"#{t}" for t in topics[:5])

    tweet = f"{title}\n\n{hashtags}\n\n{url}"

    # 280文字を超える場合はタイトルを短縮
    if len(tweet) > 280:
        suffix = f"\n\n{hashtags}\n\n{url}"
        max_title_len = 280 - len(suffix) - 3
        title = title[:max_title_len] + "..."
        tweet = f"{title}{suffix}"

    return tweet


def post_tweet(text: str) -> None:
    """Twitter API v2 でツイートを投稿する。"""
    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )
    response = client.create_tweet(text=text)
    print(f"✅ ツイート完了: https://twitter.com/i/web/status/{response.data['id']}")


def main():
    changed = get_changed_article_paths()
    if not changed:
        print("変更された記事ファイルなし。スキップ。")
        return

    tweeted = 0
    for filepath in changed:
        if not was_just_published(filepath):
            print(f"スキップ（新規公開でない）: {filepath}")
            continue

        with open(filepath, encoding="utf-8") as f:
            fm = get_frontmatter(f.read())

        slug = os.path.basename(filepath).replace(".md", "")
        tweet_text = build_tweet(fm, slug)

        print(f"投稿内容:\n{tweet_text}\n")
        post_tweet(tweet_text)
        tweeted += 1

    if tweeted == 0:
        print("新規公開された記事なし。ツイートなし。")
    else:
        print(f"{tweeted} 件ツイートしました。")


if __name__ == "__main__":
    main()
