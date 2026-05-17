"""
平日朝7:30（JST）に自動でXへ投稿するスクリプト。
GitHub Actions のスケジュール実行から呼び出される。

優先順位:
  1. content/tweet_queue.md の先頭アイデア → Claude API で投稿文生成
  2. キューが空なら最新の Zenn 記事内容 → Claude API で投稿文生成
  3. どちらもなければスキップ

投稿後:
  - キューから使ったアイデアを削除してコミット
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import tweepy

# ── 設定 ──────────────────────────────────────────────
ZENN_USERNAME   = os.environ.get("ZENN_USERNAME", "swallow_eng")
QUEUE_FILE      = Path("content/tweet_queue.md")
ARTICLES_DIR    = Path("articles")
GIT_USER_NAME   = "github-actions[bot]"
GIT_USER_EMAIL  = "github-actions[bot]@users.noreply.github.com"

# 曜日ごとのコンテンツ方針（0=月, 4=金）
WEEKDAY_THEMES = {
    0: "技術的な学びや発見。『〇〇だと思っていたが実は△△だった』のような気づき。",
    1: "実装tips・短い実践知識。エンジニアがすぐ試せる内容。",
    2: "プロジェクト進捗・開発の裏側。リアルな開発体験。",
    3: "『知ってた？』系の豆知識。部品・規格・仕組みの面白い側面。",
    4: "週末プロジェクトのテーマ提案や問いかけ。読者を巻き込む内容。",
}

PERSONA = """あなたは「swallow_eng」というペルソナの組み込みエンジニアです。
大手車載メーカー10年（前半：機構設計、後半：BLE/AOSP組み込みソフト）の経験を持ち、
個人でnRF52840+BLE+SGP40+電子ペーパーのIoTデバイスを自作・Zennで発信しています。
Fiverr・Upwork・ランサーズでBLE/組み込み開発の副業も行っています。"""
# ─────────────────────────────────────────────────────


def get_weekday_theme() -> str:
    return WEEKDAY_THEMES.get(datetime.now().weekday(), WEEKDAY_THEMES[0])


def read_queue() -> tuple[str | None, list[str]]:
    """キューの先頭アイデアと残りの行を返す。"""
    if not QUEUE_FILE.exists():
        return None, []

    lines = QUEUE_FILE.read_text(encoding="utf-8").splitlines()
    ideas = [l for l in lines if l.startswith("- ")]

    if not ideas:
        return None, lines

    first_idea = ideas[0][2:].strip()  # "- " を除去
    remaining  = [l for l in lines if l != ideas[0]]
    return first_idea, remaining


def write_queue(remaining_lines: list[str]) -> None:
    """使用済みアイデアを除いたキューを保存してコミット。"""
    # 末尾の空行を整理
    content = "\n".join(remaining_lines).rstrip() + "\n"
    QUEUE_FILE.write_text(content, encoding="utf-8")

    subprocess.run(["git", "config", "user.name",  GIT_USER_NAME],  check=True)
    subprocess.run(["git", "config", "user.email", GIT_USER_EMAIL], check=True)
    subprocess.run(["git", "add", str(QUEUE_FILE)], check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: remove used tweet idea from queue"],
        check=True
    )
    subprocess.run(["git", "push"], check=True)


def get_latest_article_excerpt() -> str | None:
    """最新の公開済み記事の冒頭部分を返す（フォールバック用）。"""
    if not ARTICLES_DIR.exists():
        return None

    mds = sorted(ARTICLES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for md in mds:
        text = md.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not fm_match:
            continue
        fm_text, body = fm_match.groups()
        if "published: true" not in fm_text:
            continue
        # 本文の最初の500文字を返す
        excerpt = body.strip()[:500]
        title_match = re.search(r'title:\s*"(.+?)"', fm_text)
        title = title_match.group(1) if title_match else "記事"
        return f"記事タイトル: {title}\n\n冒頭:\n{excerpt}"

    return None


def generate_tweet(idea: str, theme: str) -> str:
    """Claude API（Haiku）でツイート文を生成する。"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""{PERSONA}

今日のコンテンツ方針: {theme}

以下のアイデア・メモをもとに、X（Twitter）への投稿文を1つ生成してください。

アイデア:
{idea}

条件:
- 日本語（技術用語の英語はそのまま使う）
- 全体で280文字以内（ハッシュタグ含む）
- ハッシュタグを3〜5個、末尾に追加（#BLE #nRF52840 #組み込み #IoT #Arduino #組み込みLinux など適切なものを選ぶ）
- 「私は」「ぼくは」など一人称の主語は使わない
- 宣伝・自己PRにならず、読んで「へぇ」と思える内容
- 体験談・気づき・豆知識の口調で自然に

投稿文のみ出力してください（前置き・説明なし）:"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def post_to_x(text: str) -> str:
    """Twitter API v2 でツイートし、投稿URLを返す。"""
    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )
    response = client.create_tweet(text=text)
    tweet_id = response.data["id"]
    return f"https://twitter.com/i/web/status/{tweet_id}"


def main():
    # ① キューから先頭アイデアを取得
    idea, remaining = read_queue()
    source = "queue"

    # ② キューが空なら最新記事からフォールバック
    if not idea:
        idea = get_latest_article_excerpt()
        source = "article"

    if not idea:
        print("投稿するコンテンツなし。スキップします。")
        sys.exit(0)

    # ③ Claude API でツイート文を生成
    theme = get_weekday_theme()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] ソース: {source}")
    print(f"テーマ: {theme}")
    print(f"アイデア: {idea[:100]}...")

    tweet_text = generate_tweet(idea, theme)
    print(f"\n生成されたツイート（{len(tweet_text)}文字）:\n{tweet_text}\n")

    # ④ Xに投稿
    url = post_to_x(tweet_text)
    print(f"✅ 投稿完了: {url}")

    # ⑤ キューを使用済みアイデアを削除して保存
    if source == "queue":
        write_queue(remaining)
        print("キューを更新しました。")


if __name__ == "__main__":
    main()
