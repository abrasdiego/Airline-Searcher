"""Render search results as an HTML email (with a plain-text fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Optional

from airline_searcher.history import History
from airline_searcher.models import FlightOption
from airline_searcher.search import SearchResult


@dataclass
class Report:
    subject: str
    html: str
    text: str


def money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.0f}"


def duration(minutes: Optional[int]) -> str:
    if not minutes:
        return "–"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def stops_label(stops: int) -> str:
    return "Nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"


def dates_label(opt: FlightOption) -> str:
    out = opt.depart_date.strftime("%a %d %b")
    if opt.return_date:
        out += f" → {opt.return_date.strftime('%a %d %b')} ({opt.nights}n)"
    return out


def delta_label(current: float, previous: Optional[float]) -> tuple[str, str]:
    """Returns (text, colour) describing the price change."""
    if previous is None:
        return "new", "#6b7280"
    diff = current - previous
    if abs(diff) < 0.5:
        return "no change", "#6b7280"
    if diff < 0:
        return f"▼ {abs(diff):,.0f}", "#15803d"
    return f"▲ {diff:,.0f}", "#b91c1c"


TABLE = 'style="border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 20px"'
TH = 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #e5e7eb;color:#374151"'
TD = 'style="padding:6px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top"'


def _section(result: SearchResult, history: History, today: date) -> tuple[str, str]:
    c = result.config
    trip = "One-way" if c.trip_type == "one_way" else (
        f"Round trip, {c.min_nights}" + (f"–{c.max_nights}" if c.max_nights != c.min_nights else "")
        + " nights")
    window = f"{c.window_start.strftime('%d %b %Y')} – {c.window_end.strftime('%d %b %Y')}"
    header = f"{c.name}: {c.origin} → {c.destination}"
    meta = (f"{trip} · window {window} · {c.adults} adult{'s' if c.adults > 1 else ''} · "
            f"{c.cabin.replace('_', ' ')} · {result.requests_made} date combinations searched")

    html = [f'<h2 style="margin:24px 0 4px;font-size:18px">{escape(header)}</h2>',
            f'<div style="color:#6b7280;font-size:13px">{escape(meta)}</div>']
    text = [header, meta]

    best = result.best
    if best is None:
        html.append('<p><b>No flights found.</b></p>')
        text.append("No flights found.")
    else:
        prev = history.previous_best(result, today)
        lowest = history.lowest_seen(result)
        d_text, d_color = delta_label(best.price, prev)
        summary = f"Best price today: {money(best.price, best.currency)} ({dates_label(best)})"
        extra = f" — vs last run: {d_text}" if prev is not None else ""
        if lowest is not None and best.price < lowest:
            extra += " — lowest price seen so far!"
        html.append(f'<p style="font-size:16px;margin:12px 0">Best price today: '
                    f'<b>{money(best.price, best.currency)}</b> ({escape(dates_label(best))})'
                    f'<span style="color:{d_color}">{escape(extra)}</span></p>')
        text.append(summary + extra)

        # Top N overall
        html.append(f'<h3 style="font-size:15px;margin:12px 0 0">Top {len(result.top)} cheapest</h3>')
        average, source = result.route_average()
        if average is not None:
            what = "typical Google Flights price" if source == "typical" else "average of today's fares"
            html.append(f'<div style="color:#6b7280;font-size:13px">Usual price for this route: '
                        f'<b style="color:#111827">{money(average, best.currency)}</b> ({what})</div>')
            text.append(f"Usual price for this route: {money(average, best.currency)} ({what})")
        html.append(f'<table {TABLE}><tr><th {TH}>Price</th><th {TH}>Dates</th><th {TH}>Airline</th>'
                    f'<th {TH}>Stops</th><th {TH}>Duration</th></tr>')
        text.append(f"\nTop {len(result.top)} cheapest:")
        for opt in result.top:
            airline = ", ".join(opt.airlines) or "–"
            price = money(opt.price, opt.currency)
            if opt.booking_url:
                price = (f'<a href="{escape(opt.booking_url)}" style="color:#0b5cd5;text-decoration:none">'
                         f'{price}&nbsp;&#8599;</a>')
            sign, sign_text = "", ""
            if average is not None:
                below = opt.price <= average
                sign = (f'<div style="display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;'
                        f'border-radius:999px;margin-bottom:3px;'
                        f'color:{"#11805a" if below else "#c3362b"};background:{"#e2f4ec" if below else "#fbe8e6"}">'
                        f'&#9679; {"Below" if below else "Above"} average</div><br>')
                sign_text = f"  ({'below' if below else 'above'} average)"
            html.append(f'<tr><td {TD}>{sign}<b>{price}</b></td>'
                        f'<td {TD}>{escape(dates_label(opt))}</td><td {TD}>{escape(airline)}</td>'
                        f'<td {TD}>{stops_label(opt.stops)}</td><td {TD}>{duration(opt.duration_minutes)}</td></tr>')
            text.append(f"  {money(opt.price, opt.currency):>12}  {dates_label(opt)}  {airline}  "
                        f"{stops_label(opt.stops)}  {duration(opt.duration_minutes)}{sign_text}")
        html.append("</table>")

        # Price calendar: cheapest per date combination
        html.append('<h3 style="font-size:15px;margin:12px 0 0">Cheapest price per date</h3>')
        html.append(f'<table {TABLE}><tr><th {TH}>Dates</th><th {TH}>Price</th>'
                    f'<th {TH}>vs last run</th><th {TH}>Airline</th></tr>')
        text.append("\nCheapest price per date:")
        for opt in result.cheapest_by_dates():
            d_text, d_color = delta_label(opt.price, history.previous_price_for(result, opt.date_key, today))
            row_style = ' style="background:#ecfdf5"' if opt is best or opt.price == best.price else ""
            html.append(f'<tr{row_style}><td {TD}>{escape(dates_label(opt))}</td>'
                        f'<td {TD}>{money(opt.price, opt.currency)}</td>'
                        f'<td {TD}><span style="color:{d_color}">{escape(d_text)}</span></td>'
                        f'<td {TD}>{escape(", ".join(opt.airlines) or "–")}</td></tr>')
            text.append(f"  {dates_label(opt):<32} {money(opt.price, opt.currency):>12}  ({d_text})")
        html.append("</table>")

    if result.errors:
        html.append('<details><summary style="color:#b45309">'
                    f'{len(result.errors)} warning(s)</summary><ul style="font-size:12px;color:#6b7280">')
        html.extend(f"<li>{escape(e)}</li>" for e in result.errors[:20])
        html.append("</ul></details>")
        text.append(f"\nWarnings ({len(result.errors)}):")
        text.extend(f"  - {e}" for e in result.errors[:20])

    return "\n".join(html), "\n".join(text)


def build_report(results: list[SearchResult], history: History, subject_prefix: str,
                 today: Optional[date] = None) -> Report:
    today = today or date.today()
    sections = [_section(r, history, today) for r in results]

    bests = [r.best for r in results if r.best]
    if len(results) == 1 and bests:
        b = bests[0]
        subject = f"{subject_prefix}: {b.origin}→{b.destination} from {money(b.price, b.currency)}"
    else:
        subject = f"{subject_prefix} — {today.strftime('%d %b %Y')}"

    html = (
        '<!doctype html><html><body style="margin:0;padding:16px;background:#ffffff;color:#111827;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:760px;margin:0 auto">'
        f'<h1 style="font-size:20px;margin:0 0 4px">✈️ {escape(subject_prefix)}</h1>'
        f'<div style="color:#6b7280;font-size:13px">{today.strftime("%A, %d %B %Y")}</div>'
        + "".join(s[0] for s in sections)
        + '<p style="color:#9ca3af;font-size:12px;margin-top:24px">Prices are for all passengers, '
          'as reported by the provider at search time, and can change before booking.</p>'
        "</div></body></html>"
    )
    text = f"{subject_prefix} — {today.isoformat()}\n\n" + "\n\n".join(s[1] for s in sections)
    return Report(subject=subject, html=html, text=text)
