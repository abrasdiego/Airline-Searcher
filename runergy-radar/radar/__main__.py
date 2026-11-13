"""Command line.

    python -m radar serve          open the dashboard at http://localhost:8010
    python -m radar scan           run one scan now
    python -m radar demo           fill the dashboard with example conversations (no keys needed)
    python -m radar demo --clear   remove every stored conversation
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from radar.scan import ScanBusy, run_scan
from radar.store import shared_store
from radar.text import Found, evaluate, now_utc

DEMO = [
    ("https://www.reddit.com/r/running/comments/1demo01/which_running_app_for_first_half/",
     "Which running app for my first half marathon?",
     "I've been running 3x a week for about six months and just signed up for a half in April. "
     "Looking for a running app recommendation that gives me a real plan and not just tracking. "
     "Bonus if it helps with what to eat on long runs because I keep bonking around 15k.", 3,
     94, "asking_for_recommendation",
     "Runner asking for an app with a plan plus fueling help: a direct fit.",
     "Congrats on signing up! Six months of consistent running is a great base for an April half.\n\n"
     "On the bonking around 15k: that's almost always fueling rather than fitness. Try taking "
     "30-40g of carbs about every 30-40 minutes on anything over an hour, starting early (around "
     "the 30 minute mark) rather than waiting until you feel flat. Practise it on every long run "
     "so race day is nothing new.\n\n"
     "For the plan, look for something with 3 runs a week you can actually keep: one easy, one "
     "with some tempo work, one long run that builds gradually to 18-19k.\n\n"
     "Full disclosure: I'm part of the team building Runergy Lab, which pairs a training plan "
     "with fueling guidance for exactly this situation, so it may be worth a look. Either way, "
     "sort the fueling and that 15k wall should move."),
    ("https://www.reddit.com/r/C25K/comments/1demo02/finished_c25k_what_next/",
     "Finished couch to 5k last week. What next?",
     "Did it! 31 minutes. Now I don't know what to do. Should I work on speed or go for 10k? "
     "Is there a 10k training plan people here liked?", 9,
     88, "asking_for_advice", "Beginner looking for the next plan after C25K: good fit.",
     "Huge congrats, finishing C25K is the hardest part.\n\nI'd go for distance before speed. "
     "Keep running three times a week, hold two of the runs at your current 30 minutes, and add "
     "about 5 minutes to the third each week until you reach 60 minutes. That gets you to 10k in "
     "roughly 6-8 weeks, and your 5k time will drop on its own along the way.\n\n"
     "Keep every run at a pace where you could chat. It feels too slow and it works."),
    ("https://www.threads.com/@marathon.mia/post/DEMO0000003",
     "", "Week 9 of marathon training and I still have no fueling strategy. How many gels do "
     "people actually take in a marathon?? I took one on my 28k and wanted to cry at the end",
     5, 82, "asking_for_advice", "Marathoner asking a specific fueling question we can answer well.",
     "One gel over 28k would make anyone want to cry! A good starting point is 60g of carbs per "
     "hour, so roughly a gel every 25-30 minutes from early on. For most people that's 6-8 gels "
     "in a marathon. Start practising on your next long run so your stomach gets used to it."),
    ("https://www.letsrun.com/forum/flat_read.php?thread=12345678",
     "Runna vs a coach vs just using a free plan?",
     "Thinking about paying for one of the plan apps for a spring marathon. Anyone tried an "
     "alternative to Runna? Is the adaptive stuff worth it or is a Pfitz plan from the book fine?",
     20, 76, "discussing_competitor", "Comparing plan apps; a balanced, disclosed mention could fit.",
     "A book plan like Pfitz is genuinely enough for most people if you're consistent and honest "
     "about your current mileage. The value of an app is mostly convenience: paces worked out for "
     "you and adjustments when you miss a week.\n\nIf you go the app route, pick on what you "
     "struggle with. Full disclosure: I'm part of the team building Runergy Lab, which combines "
     "a training plan with fueling guidance, so I'm biased, but if nutrition is your weak spot it "
     "may be worth comparing."),
    ("https://www.reddit.com/r/AdvancedRunning/comments/1demo05/carb_loading_protocol/",
     "Carb loading: 2 days or 3?",
     "Running my A race in two weeks. What does everyone do for carb loading, and how many "
     "grams per kg? Last time I felt heavy at the start.", 30,
     71, "asking_for_advice", "Experienced runner with a nutrition question; helpful answer, no plug needed.",
     "Two days is plenty for most people. Aim for roughly 8-10g of carbs per kg of body weight "
     "per day, mostly from low-fibre foods, and cut back on fat and fibre rather than simply "
     "eating more of everything. Feeling a bit heavy at the start is normal: it's the water "
     "stored with the glycogen, and it's what you'll be glad of at 30k."),
    ("https://www.reddit.com/r/running/comments/1demo06/new_shoes_day/",
     "Best running app deals this week (affiliate roundup)",
     "Our weekly roundup of discounts on the best running app subscriptions and shoes.", 40,
     18, "other", "A promotional roundup, not a person asking for help.", ""),
]


def demo(clear: bool) -> None:
    store = shared_store()
    if clear:
        store.clear_threads()
        print("Removed every stored conversation.")
        return
    settings = store.settings()
    now = now_utc()
    added = 0
    for url, title, body, hours, score, intent, reason, draft in DEMO:
        thread = evaluate(Found(url=url, title=title, body=body, source="Example", trusted=True,
                                created_at=now - timedelta(hours=hours)), settings, now)
        if not thread or not store.add_threads([thread]):
            continue
        added += 1
        store.update_thread(thread["id"], ai_score=score, intent=intent, ai_reason=reason,
                            draft=draft, draft_state="ready" if draft else "skip")
        if not draft:
            store.set_status(thread["id"], "filtered")
    print(f"Added {added} example conversations. Open the dashboard: python -m radar serve")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m radar", description="Runergy Radar")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="open the dashboard")
    serve.add_argument("--port", type=int, default=8010)
    scan = commands.add_parser("scan", help="run one scan now")
    scan.add_argument("--seconds", type=float, default=240, help="time allowed for the scan")
    sample = commands.add_parser("demo", help="add example conversations")
    sample.add_argument("--clear", action="store_true", help="remove every stored conversation")
    args = parser.parse_args(argv)

    if args.command == "serve":
        from radar.web import serve as run_server
        run_server(args.port)
    elif args.command == "demo":
        demo(args.clear)
    else:
        try:
            summary = run_scan(shared_store(), time_budget=args.seconds)
        except ScanBusy as exc:
            print(exc)
            return 1
        print(f"Looked at {summary['seen']} results: {summary['new']} new conversations, "
              f"{summary['drafted']} replies drafted ({summary['seconds']}s).")
        for error in summary["errors"]:
            print(f"  ! {error}")
        return 1 if summary["errors"] and not summary["seen"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
