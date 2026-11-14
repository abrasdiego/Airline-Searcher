import pytest

from radar import ai, links
from radar.defaults import clean_settings, defaults
from radar.text import Found, evaluate

SITE = "https://runergylab.com"


def settings(**link):
    s = defaults()
    s["link"].update(link)
    return s


def test_default_link_is_the_website():
    assert links.reply_link(defaults(), "reddit") == SITE
    assert links.reply_link(defaults(), "bluesky", "abc") == SITE
    assert clean_settings({"brand": {"url": "runergylab.com/app"}})["brand"]["url"] == SITE + "/app"
    assert clean_settings({"brand": {"url": ""}})["brand"]["url"] == ""
    for bad in ("not a link", "ftp://x.example", "https://nodot"):
        with pytest.raises(ValueError):
            clean_settings({"brand": {"url": bad}})
    assert clean_settings({"link": {"mode": "nonsense"}})["link"] == {"mode": "when_mentioned", "track": False, "via_radar": False, "base": "", "pass_ref": True}


def test_tracking_tags_each_platform():
    s = settings(track=True)
    s["brand"]["url"] = "https://runergylab.com/get?ref=1&utm_source=old"
    assert links.reply_link(s, "reddit") == \
        "https://runergylab.com/get?ref=1&utm_source=reddit&utm_medium=community&utm_campaign=radar"
    assert "utm_source=forum" in links.reply_link(s, "web")


@pytest.mark.parametrize("draft,expected", [
    ("Try Runergy Lab.", "Try Runergy Lab.\n\n" + SITE),                          # mention, no link: added
    ("Just run easy.", "Just run easy."),                                         # no mention: no link
    ("Runergy Lab (runergylab.com) helps.", f"Runergy Lab ({SITE}) helps."),      # bare domain: made exact
    ("See http://www.runergylab.com/pricing, Runergy Lab.", f"See {SITE}, Runergy Lab."),
    (f"Runergy Lab: {SITE}.", f"Runergy Lab: {SITE}."),                           # already right: untouched
    ("Runergy Lab. Mail hi@runergylab.com", "Runergy Lab. Mail hi@runergylab.com\n\n" + SITE),
    ("", ""),
])
def test_ai_replies_carry_exactly_the_chosen_link(draft, expected):
    assert links.enforce(draft, defaults(), "reddit") == expected


def test_link_modes():
    assert links.enforce("Just run easy.", settings(mode="always"), "reddit") == "Just run easy.\n\n" + SITE
    assert links.enforce(f"Runergy Lab {SITE} is good.", settings(mode="never"), "reddit") == "Runergy Lab  is good."
    none = defaults()
    none["brand"]["url"] = ""
    assert links.enforce("Try Runergy Lab.", none, "reddit") == "Try Runergy Lab."


def test_changing_the_link_updates_unposted_replies(store):
    before = defaults()
    threads = [evaluate(Found(url=f"https://www.reddit.com/r/running/comments/{n}/x/", trusted=True,
                              title="Which running app?", body="running app"), before) for n in (1, 2, 3, 4)]
    store.add_threads(threads)
    ids = [t["id"] for t in threads]
    store.update_thread(ids[0], draft=f"Try Runergy Lab: {SITE}. Good luck!")
    store.update_thread(ids[1], draft=f"Posted already {SITE}")
    store.set_status(ids[1], "replied", f"Posted already {SITE}")
    store.update_thread(ids[2], draft=f"Read {SITE}/blog/pacing and {SITE}/")
    store.update_thread(ids[3], draft="No link here")

    after = store.save_settings({"brand": {"url": "https://runergylab.com/app"}})
    assert links.relink_drafts(store, before, after) == 2
    assert store.get_thread(ids[0])["draft"] == "Try Runergy Lab: https://runergylab.com/app. Good luck!"
    assert SITE + "/app" not in store.get_thread(ids[1])["reply_text"]          # posted replies are history
    assert store.get_thread(ids[2])["draft"] == f"Read {SITE}/blog/pacing and https://runergylab.com/app"
    assert links.relink_drafts(store, after, after) == 0

    tracked = store.save_settings({"brand": {"url": "https://apps.example/runergy"}, "link": {"track": True}})
    assert links.relink_drafts(store, after, tracked) == 2
    assert store.get_thread(ids[0])["draft"].startswith(
        "Try Runergy Lab: https://apps.example/runergy?utm_source=reddit&utm_medium=community&utm_campaign=radar.")
    plain = store.save_settings({"brand": {"url": "https://apps.example/runergy"}})
    assert links.relink_drafts(store, tracked, plain) == 2
    assert store.get_thread(ids[0])["draft"] == "Try Runergy Lab: https://apps.example/runergy. Good luck!"


def test_prompt_states_the_link_rule():
    thread = {"platform": "threads", "title": "t", "body": "b", "matched": []}
    assert "written exactly as given" in ai.system_prompt(defaults())
    assert "Do not put any link" in ai.system_prompt(settings(mode="never"))
    assert "Every reply mentions" in ai.system_prompt(settings(mode="always"))
    assert f"Link for this reply: {SITE}" in ai.user_prompt(thread, link=SITE)
    assert "do not include a link" in ai.user_prompt(thread)
