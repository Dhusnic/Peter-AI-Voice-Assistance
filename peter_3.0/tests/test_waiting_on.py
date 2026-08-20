"""Mail you sent that nobody answered.

Reply detection is a heuristic and the tests say so plainly. What must not
happen is the *unsafe* failure: a search error being read as "no reply", which
would report half your sent folder as outstanding.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.core.errors import IntegrationError
from peter.waiting_on import Outstanding, base_subject, build_waiting_on, spoken_summary


def message(subject, days_ago=5, uid="1"):
    return SimpleNamespace(
        uid=uid, subject=subject, sender="you", sender_email="you@example.com",
        date=datetime.now().astimezone() - timedelta(days=days_ago),
    )


class FakeMail:
    """Serves the Sent folder from `sent`, and inbox searches from `replies`."""

    def __init__(self, sent=(), replies=None, search_error=None):
        self.sent = list(sent)
        self.replies = replies or {}
        self.search_error = search_error
        self.searches = []

    def list_messages(self, criteria="UNSEEN", limit=25, folder=None):
        if criteria.startswith("SINCE"):
            return self.sent[:limit]
        self.searches.append(criteria)
        if self.search_error:
            raise self.search_error
        for subject, found in self.replies.items():
            if f'"{subject}"' == criteria.replace("SUBJECT ", ""):
                return found
        return []


@pytest.fixture
def mailed(container):
    def wire(**kwargs):
        mail = FakeMail(**kwargs)
        container.mail = lambda: mail
        return mail

    return wire


# ------------------------------------------------------------- base subject
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Re: Azure feature discussion", "Azure feature discussion"),
        ("RE : Azure feature discussion", "Azure feature discussion"),
        ("Fwd: Re: HR request", "HR request"),
        ("FW: production issue", "production issue"),
        ("no prefix at all", "no prefix at all"),
        ("", ""),
    ],
)
def test_reply_prefixes_are_stripped(raw, expected):
    assert base_subject(raw) == expected


# ------------------------------------------------------------------ building
def test_an_unanswered_message_is_reported(mailed):
    mailed(sent=[message("Azure feature discussion", days_ago=5)])

    items = build_waiting_on()

    assert len(items) == 1
    assert items[0].subject == "Azure feature discussion"
    assert items[0].days == 5


def test_an_answered_message_is_not_reported(mailed):
    """A reply keeps the subject with a Re: prefix, which IMAP's substring
    SUBJECT search finds for the base subject."""
    reply = message("Re: Azure feature discussion", days_ago=1)
    mailed(
        sent=[message("Azure feature discussion", days_ago=5)],
        replies={"Azure feature discussion": [reply]},
    )

    assert build_waiting_on() == []


def test_a_reply_older_than_the_sent_message_does_not_count(mailed):
    """An earlier message in the same thread is not an answer to this one."""
    older = message("Re: Azure feature discussion", days_ago=9)
    mailed(
        sent=[message("Azure feature discussion", days_ago=5)],
        replies={"Azure feature discussion": [older]},
    )

    assert len(build_waiting_on()) == 1


def test_a_recent_message_has_not_had_a_fair_chance_yet(mailed):
    mailed(sent=[message("sent this morning", days_ago=0)])
    assert build_waiting_on(quiet_days=3) == []


def test_only_the_newest_message_per_thread_is_reported(mailed):
    """Three mails in one thread is one thing you are waiting on, not three."""
    mailed(sent=[
        message("Re: Azure feature discussion", days_ago=4),
        message("Azure feature discussion", days_ago=6),
    ])

    assert len(build_waiting_on()) == 1


def test_a_very_short_subject_is_ignored(mailed):
    """A two-character subject would match half the inbox on a substring
    search, so it is not worth reporting on."""
    mailed(sent=[message("ok", days_ago=5)])
    assert build_waiting_on() == []


def test_a_failed_reply_search_assumes_answered_not_outstanding(mailed):
    """The unsafe failure would be reporting everything as unanswered because
    the check itself broke."""
    mailed(
        sent=[message("Azure feature discussion", days_ago=5)],
        search_error=IntegrationError("search failed", service="mail"),
    )

    assert build_waiting_on() == []


def test_results_are_ordered_oldest_first(mailed):
    mailed(sent=[
        message("recent one", days_ago=4),
        message("ancient one", days_ago=20),
    ])

    items = build_waiting_on()

    assert [i.days for i in items] == [20, 4]


def test_an_unreachable_mailbox_propagates(container):
    def boom():
        raise IntegrationError("imap down", service="mail")

    container.mail = boom

    with pytest.raises(IntegrationError):
        build_waiting_on()


def test_the_sent_folder_from_config_is_the_one_read(mailed, container):
    mail = mailed(sent=[])
    container.config.integrations.mail.sent_folder = "[Gmail]/Sent Mail"
    folders = []
    original = mail.list_messages

    def spy(criteria="UNSEEN", limit=25, folder=None):
        folders.append(folder)
        return original(criteria, limit, folder)

    mail.list_messages = spy

    build_waiting_on()

    assert folders[0] == "[Gmail]/Sent Mail"


# ----------------------------------------------------------------- rendering
def test_an_empty_list_reads_as_nothing_outstanding():
    assert spoken_summary([]) == "Nothing is waiting on a reply."


def test_the_summary_names_the_oldest_few():
    items = [
        Outstanding("Azure feature discussion", None, 9),
        Outstanding("HR request", None, 5),
    ]
    text = spoken_summary(items)

    assert "2 messages you sent got no reply" in text
    assert "Azure feature discussion — sent 9 days ago" in text


def test_the_summary_caps_how_many_it_names():
    items = [Outstanding(f"thread {i}", None, i) for i in range(10)]
    text = spoken_summary(items, limit=2)

    assert "and 8 more" in text


def test_one_day_is_not_pluralised():
    assert "sent 1 day ago" in Outstanding("x", None, 1).spoken()
