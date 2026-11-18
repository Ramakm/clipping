import json

import pytest

from captions import Word, group_words, words_to_ass
from chat import parse_stream_url
from chat.replay import load_chat_file
from chat.twitch import parse_privmsg
from config import FacecamBox
from editor import build_filtergraph, caption_y, facecam_height, valid_facecam
from facecam import suggest_box
from pipeline import next_clip_number, slug, suggest_title


# ---- chat ------------------------------------------------------------------

def test_parse_privmsg():
    line = ":coolguy!coolguy@coolguy.tmi.twitch.tv PRIVMSG #streamer :KEKW that was insane"
    assert parse_privmsg(line) == ("coolguy", "KEKW that was insane")
    tagged = "@badge-info=;color=#FF0000 :a!a@a.tmi.twitch.tv PRIVMSG #s :hi"
    assert parse_privmsg(tagged) == ("a", "hi")
    assert parse_privmsg("PING :tmi.twitch.tv") is None
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan123 :Welcome") is None


@pytest.mark.parametrize("url,platform,channel", [
    ("https://twitch.tv/SomeStreamer", "twitch", "somestreamer"),
    ("https://www.twitch.tv/xqc/", "twitch", "xqc"),
    ("twitch.tv/foo", "twitch", "foo"),
    ("https://youtube.com/watch?v=abc123", "youtube", "abc123"),
    ("https://www.youtube.com/live/abc123?si=x", "youtube", "abc123"),
    ("https://youtu.be/abc123", "youtube", "abc123"),
    ("https://www.youtube.com/@Handle/live", "youtube", "@handle"),
])
def test_parse_stream_url(url, platform, channel):
    t = parse_stream_url(url)
    assert (t.platform, t.channel) == (platform, channel)


def test_parse_stream_url_rejects_unknown():
    with pytest.raises(ValueError):
        parse_stream_url("https://example.com/stream")


def test_load_chat_file_formats(tmp_path):
    p = tmp_path / "chat.json"
    p.write_text(json.dumps([
        {"t": 5, "user": "b", "text": "second"},
        {"time_in_seconds": 1.5, "author": {"name": "a"}, "message": "first"},
        {"junk": True},
    ]))
    msgs = load_chat_file(p)
    assert [(m.ts, m.user, m.text) for m in msgs] == [(1.5, "a", "first"), (5.0, "b", "second")]


# ---- captions --------------------------------------------------------------

WORDS = [Word(0.0, 0.3, "no"), Word(0.3, 0.6, "way"), Word(0.6, 1.0, "dude!"),
         Word(1.1, 1.4, "did"), Word(1.4, 1.6, "you"), Word(3.0, 3.4, "see")]


def test_group_words_respects_max_words_gaps_and_punctuation():
    groups = group_words(WORDS, max_words=3, max_gap=0.6)
    assert [[w.text for w in g] for g in groups] == [["no", "way", "dude!"], ["did", "you"], ["see"]]


def test_words_to_ass(cfg):
    ass = words_to_ass(WORDS, cfg.captions, 1080, 1920, 634)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == len(WORDS)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "\\pos(540,634)" in events[0]
    # first event highlights only the first word
    assert f"{{\\c{cfg.captions.highlight_color}}}NO{{" in events[0]
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.30")


# ---- editor ----------------------------------------------------------------

def test_facecam_filtergraph(cfg):
    box = FacecamBox(10, 20, 480, 270)
    g = build_filtergraph(cfg.editor, box, "captions.ass", has_audio=True)
    top = facecam_height(cfg.editor)
    assert "crop=480:270:10:20" in g
    assert f"crop=1080:{top}" in g and f"crop=1080:{1920 - top}" in g
    assert "vstack=inputs=2" in g and "ass=captions.ass" in g
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in g
    assert caption_y(cfg.editor, box) == top


def test_blur_filtergraph_without_audio(cfg):
    g = build_filtergraph(cfg.editor, None, None, has_audio=False)
    assert "boxblur" in g and "overlay" in g
    assert "[0:a]" not in g and "ass=" not in g


def test_valid_facecam_rejects_out_of_frame():
    assert valid_facecam(FacecamBox(1800, 0, 480, 270), 1920, 1080) is None
    assert valid_facecam(FacecamBox(0, 0, 480, 270), 1920, 1080) is not None


def test_suggest_box_fits_aspect_and_frame():
    box = suggest_box((1700, 900, 100, 100), 1920, 1080, aspect=1080 / 634, expand=2.4)
    assert box.x + box.w <= 1920 and box.y + box.h <= 1080
    assert box.w / box.h == pytest.approx(1080 / 634, rel=0.02)


# ---- pipeline --------------------------------------------------------------

def test_titles():
    assert suggest_title("xqc", "No way he hit that. Insane.", ("KEKW",)) == "No way he hit that | xqc"
    assert suggest_title("xqc", "", ("clip it",)) == "CLIP IT | xqc"
    assert len(suggest_title("xqc", "", ("x" * 200,))) <= 70


def test_slug_and_numbering(tmp_path):
    assert slug("CLIP IT") == "clip-it"
    assert next_clip_number(tmp_path) == 1
    (tmp_path / "clip_007_kekw.mp4").touch()
    assert next_clip_number(tmp_path) == 8


def test_parse_ytdlp_live_chat_line():
    from chat.youtube import parse_ytdlp_line
    line = json.dumps({"replayChatItemAction": {"actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": "NO WAY "}, {"emoji": {"emojiId": "x", "shortcuts": [":fire:"]}}]},
            "authorName": {"simpleText": "@bob"}, "timestampUsec": "1700000000500000"}}}},
        {"addChatItemAction": {"item": {"giftMessageViewModel": {}}}},
    ]}}).encode()
    msgs = list(parse_ytdlp_line(line))
    assert len(msgs) == 1
    assert (msgs[0].user, msgs[0].text, msgs[0].ts) == ("@bob", "NO WAY :fire:", 1700000000.5)
    assert list(parse_ytdlp_line(b"not json")) == []


def test_config_chat_delay(cfg):
    assert cfg.chat_delay["youtube"] > cfg.chat_delay["twitch"]
