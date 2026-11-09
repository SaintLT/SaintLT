#!/usr/bin/env python3
"""Generate the profile README stat cards as static SVG files.

Data comes from the GitHub GraphQL API for the profile owner. Nothing is
estimated or filled in: every number on every card is read from the API.
Uses the Python standard library only.

Environment:
  GH_TOKEN    token for the GraphQL API (the workflow passes GITHUB_TOKEN, or
              the optional PROFILE_TOKEN secret to include private contributions)
  GH_LOGIN    GitHub username to report on

Output (written only after every request has succeeded):
  assets/generated/stats.svg
  assets/generated/languages.svg
  assets/generated/streak-total.svg, streak-current.svg, streak-longest.svg
  assets/generated/activity.svg
"""
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.request
from xml.sax.saxutils import escape

API = "https://api.github.com/graphql"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "generated")

BG = "#0D1117"
BORDER = "#1F2A37"
TEXT = "#E6EDF3"
MUTED = "#8B98A5"
ACCENT = "#268BFF"
ACCENT_DIM = "#16365C"
FONT = "'Segoe UI', -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def gql(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats-generator",
        },
    )
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            if "errors" in payload:
                raise RuntimeError(payload["errors"])
            return payload["data"]
        except Exception as exc:  # retry on network or transient API errors
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"GitHub API request failed: {last}")


PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      contributionCalendar { totalContributions }
    }
  }
}
"""

YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch(login, token):
    user = gql(PROFILE_QUERY, {"login": login}, token)["user"]
    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)

    days = {}
    for year in range(created.year, now.year + 1):
        start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        end = min(dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
        cal = gql(
            YEAR_QUERY,
            {"login": login, "from": start.isoformat(), "to": end.isoformat()},
            token,
        )["user"]["contributionsCollection"]["contributionCalendar"]
        for week in cal["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    repos = user["repositories"]["nodes"]
    langs = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = langs.setdefault(name, {"size": 0, "color": edge["node"]["color"] or ACCENT})
            entry["size"] += edge["size"]

    recent = user["contributionsCollection"]
    return {
        "created": created.date(),
        "followers": user["followers"]["totalCount"],
        "repos": user["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "pull_requests": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "commits_year": recent["totalCommitContributions"],
        "contrib_year": recent["contributionCalendar"]["totalContributions"],
        "days": days,
        "languages": langs,
    }


# --------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------
def streaks(days, today):
    ordered = sorted(days)
    longest = run = 0
    longest_end = None
    prev = None
    run_start = None
    longest_start = None
    for d in ordered:
        date = dt.date.fromisoformat(d)
        if days[d] > 0:
            if prev is not None and (date - prev).days == 1 and run > 0:
                run += 1
            else:
                run = 1
                run_start = date
            if run > longest:
                longest, longest_start, longest_end = run, run_start, date
        else:
            run = 0
        prev = date

    cursor = today
    if days.get(cursor.isoformat(), 0) == 0:
        cursor -= dt.timedelta(days=1)  # today may simply not have a contribution yet
    current = 0
    current_end = cursor
    while days.get(cursor.isoformat(), 0) > 0:
        current += 1
        cursor -= dt.timedelta(days=1)
    current_start = cursor + dt.timedelta(days=1)
    return {
        "current": current,
        "current_range": (current_start, current_end) if current else None,
        "longest": longest,
        "longest_range": (longest_start, longest_end) if longest else None,
    }


def short(n):
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_date(d):
    return d.strftime("%b %-d, %Y")


def fmt_range(rng):
    if not rng:
        return "No active streak"
    a, b = rng
    if a == b:
        return fmt_date(a)
    return f"{fmt_date(a)} - {fmt_date(b)}"


# --------------------------------------------------------------------------
# SVG helpers
# --------------------------------------------------------------------------
def svg_open(w, h, title, desc):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-labelledby="t d" font-family="{FONT}">\n'
        f"<title id=\"t\">{escape(title)}</title><desc id=\"d\">{escape(desc)}</desc>\n"
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="10" fill="{BG}" stroke="{BORDER}"/>\n'
    )


def heading(text, right=None, w=0):
    out = f'<text x="24" y="36" font-size="16" font-weight="600" fill="{TEXT}">{escape(text)}</text>\n'
    out += f'<rect x="24" y="46" width="28" height="2" rx="1" fill="{ACCENT}"/>\n'
    if right:
        out += f'<text x="{w - 24}" y="36" font-size="13" text-anchor="end" fill="{MUTED}">{escape(right)}</text>\n'
    return out


def stats_card(d):
    w, h = 480, 200
    tiles = [
        (short(d["repos"]), "Public repositories"),
        (short(d["stars"]), "Stars earned"),
        (short(d["followers"]), "Followers"),
        (short(d["contrib_year"]), "Contributions, last year"),
        (short(d["commits_year"]), "Commits, last year"),
        (short(d["pull_requests"]), "Pull requests"),
    ]
    out = svg_open(w, h, "GitHub statistics", "Repositories, stars, followers and recent contribution counts")
    out += heading("GitHub Statistics")
    for i, (value, label) in enumerate(tiles):
        x = 24 + (i % 3) * 150
        y = 98 + (i // 3) * 60
        out += f'<text x="{x}" y="{y}" font-size="26" font-weight="600" fill="{TEXT}">{escape(value)}</text>\n'
        out += f'<text x="{x}" y="{y + 20}" font-size="11.5" fill="{MUTED}">{escape(label)}</text>\n'
    return out + "</svg>\n"


def languages_card(d):
    w, h = 480, 200
    total = sum(v["size"] for v in d["languages"].values()) or 1
    ranked = sorted(d["languages"].items(), key=lambda kv: kv[1]["size"], reverse=True)
    top = ranked[:5]
    rest = sum(v["size"] for _, v in ranked[5:])
    items = [(n, v["size"], v["color"]) for n, v in top]
    if rest:
        items.append(("Other", rest, MUTED))

    out = svg_open(w, h, "Most used languages", "Share of code by language across public repositories")
    out += heading("Most Used Languages")
    out += f'<clipPath id="bar"><rect x="24" y="64" width="432" height="10" rx="5"/></clipPath>\n<g clip-path="url(#bar)">\n'
    x = 24.0
    for name, size, color in items:
        seg = 432 * size / total
        out += f'<rect x="{x:.2f}" y="64" width="{seg:.2f}" height="10" fill="{color}"/>\n'
        x += seg
    out += "</g>\n"
    for i, (name, size, color) in enumerate(items):
        col, row = divmod(i, 3)
        lx = 24 + col * 226
        ly = 108 + row * 28
        pct = 100 * size / total
        label = "<1%" if pct < 1 else f"{pct:.1f}%"
        out += f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{color}"/>\n'
        out += f'<text x="{lx + 18}" y="{ly}" font-size="13" fill="{TEXT}">{escape(name)}</text>\n'
        out += f'<text x="{lx + 200}" y="{ly}" font-size="13" text-anchor="end" fill="{MUTED}">{label}</text>\n'
    return out + "</svg>\n"


def streak_cards(d, s):
    """Three small cards so they wrap into one column on narrow screens."""
    w, h = 290, 158
    cx = w / 2
    total = sum(d["days"].values())
    joined = d["created"].strftime("%b %Y")

    def shell(title, desc):
        return svg_open(w, h, title, desc)

    def caption(label, sub):
        return (
            f'<text x="{cx}" y="116" font-size="14" font-weight="500" text-anchor="middle" fill="{ACCENT}">{escape(label)}</text>\n'
            f'<text x="{cx}" y="138" font-size="12" text-anchor="middle" fill="{MUTED}">{escape(sub)}</text>\n'
        )

    def number(value):
        return f'<text x="{cx}" y="84" font-size="42" font-weight="600" text-anchor="middle" fill="{TEXT}">{value}</text>\n'

    total_svg = shell("Total contributions", "Contributions since joining GitHub")
    total_svg += number(f"{total:,}") + caption("Total contributions", f"{joined} - Present") + "</svg>\n"

    r = 34
    frac = min(s["current"] / max(s["longest"], 1), 1.0)
    circ = 2 * math.pi * r
    cur_svg = shell("Current streak", "Consecutive days with at least one contribution")
    cur_svg += f'<circle cx="{cx}" cy="58" r="{r}" fill="none" stroke="{ACCENT_DIM}" stroke-width="6"/>\n'
    cur_svg += (
        f'<circle cx="{cx}" cy="58" r="{r}" fill="none" stroke="{ACCENT}" stroke-width="6" stroke-linecap="round" '
        f'stroke-dasharray="{circ * frac:.1f} {circ:.1f}" transform="rotate(-90 {cx} 58)"/>\n'
    )
    cur_svg += f'<text x="{cx}" y="68" font-size="26" font-weight="600" text-anchor="middle" fill="{TEXT}">{s["current"]}</text>\n'
    cur_svg += caption("Current streak (days)", fmt_range(s["current_range"])) + "</svg>\n"

    long_svg = shell("Longest streak", "Longest run of consecutive contribution days")
    long_svg += number(str(s["longest"])) + caption("Longest streak (days)", fmt_range(s["longest_range"])) + "</svg>\n"
    return {"streak-total.svg": total_svg, "streak-current.svg": cur_svg, "streak-longest.svg": long_svg}


def smooth_path(points, y_min=None, y_max=None):
    """Catmull-Rom spline converted to cubic Bezier segments (y clamped to the plot)."""
    def clamp(y):
        if y_min is not None:
            y = max(y, y_min)
        if y_max is not None:
            y = min(y, y_max)
        return y

    if len(points) < 3:
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
    path = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, clamp(p1[1] + (p2[1] - p0[1]) / 6))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, clamp(p2[1] - (p3[1] - p1[1]) / 6))
        path += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return path


def activity_card(d, today):
    w, h = 720, 270
    left, right, top, bottom = 52, 24, 72, 40
    plot_w, plot_h = w - left - right, h - top - bottom

    start = today - dt.timedelta(days=363)
    start -= dt.timedelta(days=(start.weekday() + 1) % 7)  # back to the Sunday
    weeks = []
    cursor = start
    while cursor <= today:
        total = sum(d["days"].get((cursor + dt.timedelta(days=i)).isoformat(), 0) for i in range(7))
        weeks.append((cursor, total))
        cursor += dt.timedelta(days=7)

    peak = max((t for _, t in weeks), default=0)
    step = max(1, math.ceil(peak / 4 / 5) * 5) if peak else 5
    y_max = step * 4

    def px(i):
        return left + plot_w * i / max(len(weeks) - 1, 1)

    def py(v):
        return top + plot_h * (1 - v / y_max)

    points = [(px(i), py(t)) for i, (_, t) in enumerate(weeks)]
    line = smooth_path(points, top, top + plot_h)
    area = f"{line} L{points[-1][0]:.1f},{top + plot_h} L{points[0][0]:.1f},{top + plot_h} Z"
    year_total = sum(t for _, t in weeks)

    out = svg_open(w, h, "Contribution activity", "Weekly contributions over the last year")
    out += (
        f'<defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="0.38"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></linearGradient></defs>\n'
    )
    out += heading("Contribution Activity", f"{year_total:,} contributions in the last year", w)
    for i in range(5):
        v = step * i
        y = py(v)
        out += f'<line x1="{left}" y1="{y:.1f}" x2="{w - right}" y2="{y:.1f}" stroke="{BORDER}" stroke-dasharray="2 4"/>\n'
        out += f'<text x="{left - 10}" y="{y + 4:.1f}" font-size="12" text-anchor="end" fill="{MUTED}">{v}</text>\n'
    last_month = None
    for i, (wk, _) in enumerate(weeks):
        label = wk.strftime("%b")
        if label != last_month and (i == 0 or wk.day <= 7):
            if px(i) < w - right - 24:
                out += f'<text x="{px(i):.1f}" y="{h - 14}" font-size="12" fill="{MUTED}">{label}</text>\n'
            last_month = label
    out += f'<path d="{area}" fill="url(#fill)"/>\n'
    out += f'<path d="{line}" fill="none" stroke="{ACCENT}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>\n'
    return out + "</svg>\n"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def sample_data():
    """Layout preview only. Never used by the workflow."""
    today = dt.date.today()
    days = {}
    for i in range(400):
        day = today - dt.timedelta(days=i)
        days[day.isoformat()] = (i * 7 % 11) if i % 3 else 0
    return {
        "created": today - dt.timedelta(days=400),
        "followers": 12, "repos": 9, "stars": 34, "pull_requests": 8, "issues": 3,
        "commits_year": 210, "contrib_year": sum(days.values()), "days": days,
        "languages": {
            "Python": {"size": 52000, "color": "#3572A5"},
            "TypeScript": {"size": 31000, "color": "#3178c6"},
            "HTML": {"size": 24000, "color": "#e34c26"},
            "CSS": {"size": 9000, "color": "#563d7c"},
            "JavaScript": {"size": 7000, "color": "#f1e05a"},
            "PLpgSQL": {"size": 3000, "color": "#336790"},
            "Shell": {"size": 800, "color": "#89e051"},
        },
    }


def main():
    if "--sample" in sys.argv:
        data = sample_data()
    else:
        token = os.environ.get("GH_TOKEN")
        login = os.environ.get("GH_LOGIN")
        if not token or not login:
            raise SystemExit("GH_TOKEN and GH_LOGIN must be set")
        data = fetch(login, token)

    today = dt.datetime.now(dt.timezone.utc).date()
    s = streaks(data["days"], today)
    files = {
        "stats.svg": stats_card(data),
        "languages.svg": languages_card(data),
        "activity.svg": activity_card(data, today),
    }
    files.update(streak_cards(data, s))
    out_dir = os.path.abspath(OUT_DIR if "--sample" not in sys.argv else "/tmp/sample-cards")
    # Recover cleanly if a previous commit accidentally created this path as a file.
    if os.path.lexists(out_dir) and not os.path.isdir(out_dir):
        if os.path.islink(out_dir):
            os.unlink(out_dir)
        else:
            os.remove(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    print(f"Wrote {len(files)} cards to {out_dir}")


if __name__ == "__main__":
    main()
