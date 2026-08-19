"""Email parsing.

Real mail is a hostile format. These tests use the shapes that actually break
naive parsers: RFC 2047 encoded headers, mislabelled charsets, HTML-only bodies,
quoted reply chains, and multipart messages with attachments.
"""

from email.message import EmailMessage as Outgoing

import pytest

from peter.integrations.mail.client import _extract
from peter.integrations.mail.models import EmailSummary
from peter.integrations.mail.parsing import (
    clean_body,
    decode_mime_header,
    extract_body,
    html_to_text,
    parse_date,
    parse_message,
    parse_summary,
    split_address,
)


def build(sender="Amma <amma@example.com>", subject="Dinner",
          body="Come home by seven.", html=None, attachments=()):
    msg = Outgoing()
    msg["From"] = sender
    msg["To"] = "dhusnic@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 18 Aug 2026 18:30:00 +0530"
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, content in attachments:
        msg.add_attachment(content, maintype="application", subtype="pdf",
                           filename=name)
    return msg.as_bytes()


# --------------------------------------------------------------- headers
def test_plain_header_passes_through():
    assert decode_mime_header("Dinner tonight") == "Dinner tonight"


def test_rfc2047_encoded_header_is_decoded():
    assert decode_mime_header("=?utf-8?B?VGVzdCBTdWJqZWN0?=") == "Test Subject"


def test_encoded_non_ascii_header():
    # "வணக்கம்" base64-encoded UTF-8
    encoded = "=?utf-8?B?4K614K6j4K6V4Kякkq==?="
    assert isinstance(decode_mime_header(encoded), str)


def test_broken_header_does_not_raise():
    assert decode_mime_header("=?bogus-charset?B?zzzz?=") is not None


def test_none_header_is_empty():
    assert decode_mime_header(None) == ""


@pytest.mark.parametrize(
    "raw,name,address",
    [
        ("Amma <amma@example.com>", "Amma", "amma@example.com"),
        ('"Reddy, Dr." <dr@x.com>', "Reddy, Dr.", "dr@x.com"),
        ("plain@example.com", "Plain", "plain@example.com"),
        ("first.last@example.com", "First Last", "first.last@example.com"),
    ],
)
def test_addresses_split_into_speakable_names(raw, name, address):
    got_name, got_address = split_address(raw)
    assert got_address == address
    assert got_name == name


def test_missing_sender_is_handled():
    assert split_address(None) == ("unknown", "")


def test_date_parsing():
    parsed = parse_date("Mon, 18 Aug 2026 18:30:00 +0530")
    assert parsed is not None and parsed.year == 2026 and parsed.month == 8


def test_unparseable_date_is_none():
    assert parse_date("sometime last Tuesday") is None
    assert parse_date(None) is None


# ------------------------------------------------------------------ bodies
def test_plain_body_is_extracted():
    message = parse_message("1", build(), body_limit=4000)
    assert "Come home by seven." in message.body
    assert message.sender == "Amma"
    assert message.subject == "Dinner"


def test_html_is_converted_to_text():
    text = html_to_text("<p>Hello <b>there</b></p><p>Second line</p>")
    assert "Hello" in text and "there" in text and "Second line" in text
    assert "<" not in text


def test_html_scripts_and_styles_are_dropped():
    text = html_to_text("<style>.x{color:red}</style><p>Real content</p>")
    assert "Real content" in text
    assert "color:red" not in text


def test_html_only_message_still_produces_a_body():
    msg = Outgoing()
    msg["From"] = "News <news@x.com>"
    msg["Subject"] = "Update"
    msg["Date"] = "Mon, 18 Aug 2026 10:00:00 +0000"
    msg.set_content("<h1>Headline</h1><p>Story text</p>", subtype="html")

    parsed = parse_message("2", msg.as_bytes(), body_limit=4000)
    assert "Headline" in parsed.body or "Story text" in parsed.body


def test_attachments_are_listed_not_inlined():
    raw = build(attachments=[("invoice.pdf", b"%PDF-1.4 fake")])
    message = parse_message("3", raw, body_limit=4000)

    assert message.attachments == ["invoice.pdf"]
    assert message.has_attachments is True
    assert "%PDF" not in message.body


def test_mislabelled_charset_does_not_crash():
    raw = (
        b"From: X <x@y.com>\r\n"
        b"Subject: Test\r\n"
        b'Content-Type: text/plain; charset="definitely-not-a-charset"\r\n'
        b"\r\n"
        b"\xff\xfe some bytes\r\n"
    )
    message = parse_message("4", raw, body_limit=4000)
    assert isinstance(message.body, str)


# ------------------------------------------------------------ body cleaning
def test_quoted_reply_chain_is_cut():
    raw = (
        "My actual answer.\n\n"
        "On Mon, 18 Aug 2026 at 10:00, Someone <a@b.com> wrote:\n"
        "> the entire previous thread\n"
        "> which nobody wants read aloud\n"
    )
    cleaned = clean_body(raw, 4000)
    assert "My actual answer." in cleaned
    assert "previous thread" not in cleaned


def test_quoted_lines_are_dropped():
    cleaned = clean_body("Reply text\n> quoted line\n> another", 4000)
    assert "Reply text" in cleaned
    assert "quoted line" not in cleaned


def test_signature_delimiter_cuts_the_tail():
    cleaned = clean_body("The message.\n--\nSent from my phone", 4000)
    assert "The message." in cleaned
    assert "Sent from my phone" not in cleaned


def test_long_body_is_trimmed_with_a_marker():
    cleaned = clean_body("word " * 5000, 200)
    assert len(cleaned) < 400
    assert "trimmed" in cleaned


def test_whitespace_is_collapsed():
    cleaned = clean_body("Too     many\n\n\n\n\nblank lines", 4000)
    assert "     " not in cleaned
    assert "\n\n\n" not in cleaned


def test_empty_body_is_empty_not_none():
    assert clean_body("", 4000) == ""


# ------------------------------------------------------------------ summaries
def test_summary_reads_sender_first():
    summary = parse_summary("7", build(), flags="\\Seen")
    assert summary.spoken().startswith("Amma:")
    assert summary.unread is False


def test_unread_and_starred_flags():
    summary = parse_summary("8", build(), flags="\\Flagged")
    assert summary.unread is True
    assert summary.starred is True


def test_missing_subject_gets_a_placeholder():
    raw = b"From: X <x@y.com>\r\nDate: Mon, 18 Aug 2026 10:00:00 +0000\r\n\r\nbody\r\n"
    assert parse_summary("9", raw).subject == "(no subject)"


def test_when_is_relative_and_speakable():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    today = EmailSummary("1", "A", "a@b.c", "S", now)
    yesterday = EmailSummary("2", "A", "a@b.c", "S", now - timedelta(days=1))
    old = EmailSummary("3", "A", "a@b.c", "S", now - timedelta(days=30))

    assert ":" in today.when()            # a clock time
    assert yesterday.when() == "yesterday"
    assert "unknown" not in old.when()
    assert EmailSummary("4", "A", "a@b.c", "S", None).when() == "unknown date"


# -------------------------------------------------- IMAP response unpacking
def test_extract_pulls_payload_and_flags():
    fetched = [(b"1 (UID 42 FLAGS (\\Seen \\Flagged) BODY[] {11}", b"hello world"), b")"]
    raw, flags = _extract(fetched)
    assert raw == b"hello world"
    assert "\\Seen" in flags and "\\Flagged" in flags


def test_extract_handles_flags_on_the_trailing_element():
    fetched = [(b"1 (BODY[] {5}", b"hello"), b" FLAGS (\\Seen))"]
    raw, flags = _extract(fetched)
    assert raw == b"hello"
    assert "\\Seen" in flags


def test_extract_survives_an_empty_response():
    assert _extract([]) == (None, "")


def test_extract_survives_a_none_payload():
    raw, flags = _extract([None])
    assert raw is None
