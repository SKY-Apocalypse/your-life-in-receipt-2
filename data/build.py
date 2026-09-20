"""
build.py — turns three raw exports into one derived "life graph".

Inputs
  archive/spotify_history.csv                      ~150k plays, 2013-2024
  archive__1_/Daily Household Transactions.csv     ~2.4k spends, 2015-2018
  archive__2_/Augmented_IndiaTransactMultiFacet... card spend, 2022-2024

Output
  data.json  — receipts, threads, moments, chapters, patterns
"""

import json, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

# Raw exports go in data/raw/ (see README for where to download each one).
# This script is run from the data/ directory: `cd data && python3 build.py`
RAW = Path(__file__).parent / "raw"
OUT = Path(__file__).parent / "data.json"
SESSION_GAP_MIN = 45          # minutes of silence that ends a listening session
MOMENT_WINDOW_H = 3           # receipts within this window may share a moment
MOMENT_MAX = 9                # stop a cluster from swallowing a whole day


# ─────────────────────────────────────────────────────────── load ──

def load_music():
    df = pd.read_csv(RAW / "spotify_history.csv")
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.dropna(subset=["track_name", "artist_name"]).sort_values("ts")
    df["min_played"] = df.ms_played / 60000
    return df.reset_index(drop=True)


def load_spend():
    df = pd.read_csv(RAW / "Daily Household Transactions.csv")
    df["ts"] = pd.to_datetime(df.Date, format="mixed", dayfirst=True)
    df = df.dropna(subset=["ts"]).sort_values("ts")
    df["Note"] = df.Note.fillna("").str.strip()
    df["Subcategory"] = df.Subcategory.fillna("").str.strip()
    return df.reset_index(drop=True)


def load_card():
    df = pd.read_csv(RAW / "Augmented_IndiaTransactMultiFacet2024.csv",
                     low_memory=True)
    df["ts"] = pd.to_datetime(df.trans_date_trans_time, format="mixed",
                              errors="coerce")
    df = df.dropna(subset=["ts", "category", "amt"])
    df = df[df.ts >= "2022-01-01"].sort_values("ts")
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────── music sessions ──

def build_sessions(music):
    """Collapse 150k plays into listening sessions — the real unit of a night."""
    gap = music.ts.diff().dt.total_seconds().div(60).fillna(1e9)
    music = music.assign(sid=(gap > SESSION_GAP_MIN).cumsum())

    rows = []
    for sid, g in music.groupby("sid"):
        artists = g.artist_name.value_counts()
        tracks = g.track_name.value_counts()
        start = g.ts.iloc[0]
        rows.append(dict(
            sid=int(sid),
            start=start,
            end=g.ts.iloc[-1],
            plays=len(g),
            minutes=round(g.min_played.sum(), 1),
            top_artist=artists.index[0],
            artist_share=round(artists.iloc[0] / len(g), 2),
            n_artists=int(artists.size),
            top_track=tracks.index[0],
            top_track_n=int(tracks.iloc[0]),
            hour=int(start.hour),
            skip_rate=round(float(g.skipped.mean()), 2),
            platform=g.platform.mode().iloc[0],
        ))
    s = pd.DataFrame(rows)
    s["night"] = (s.hour >= 23) | (s.hour < 5)
    # significance: long, focused, or late sessions matter most
    s["weight"] = (np.log1p(s.minutes) * 2
                   + s.artist_share * 3
                   + s.night * 2.5
                   + (s.top_track_n >= 4) * 3)
    return s


# ───────────────────────────────────────────────────────── receipts ──

CAT_ICONS = {
    "music": "music", "purchase": "purchase", "place": "place",
    "media": "media", "note": "note", "event": "event",
}

PLACE_RE = re.compile(r"(Place [0-9A-Z]+|Permanent Residence|Current Residence|"
                      r"dadar|Santacruz|sevagram|bus stand|airport|station)", re.I)

MEDIA_WORDS = ("netflix", "hotstar", "prime", "tata sky", "movie", "cinema",
               "theatre", "spotify", "youtube", "subscription")


def spend_receipts(spend):
    out = []
    for i, r in spend.iterrows():
        cat = str(r.Category).strip()
        sub = r.Subcategory
        note = r.Note
        blob = f"{cat} {sub} {note}".lower()

        kind = "purchase"
        if any(w in blob for w in MEDIA_WORDS):
            kind = "media"
        elif PLACE_RE.search(note or ""):
            kind = "place"
        elif r["Income/Expense"] != "Expense":
            kind = "event"

        title = sub or cat
        out.append(dict(
            id=f"s{i}", type=kind, t=r.ts.isoformat(timespec="minutes"),
            title=title[:60], note=note[:90], category=cat,
            dayonly=bool(r.ts.hour == 0 and r.ts.minute == 0),
            amount=None if pd.isna(r.Amount) else round(float(r.Amount), 2),
            mode=str(r.Mode), flow=r["Income/Expense"], source="household",
        ))
    return out


def card_receipts(card, per_month=14):
    """Sample a plausible personal card history for the 2022-24 stretch."""
    card = card.copy()
    card["m"] = card.ts.dt.to_period("M").astype(str)
    keep = (card.groupby("m", group_keys=False)
                .apply(lambda g: g.sample(min(len(g), per_month), random_state=7)))
    out = []
    for i, r in keep.sort_values("ts").iterrows():
        cat = str(r.category)
        kind = {"entertainment": "media", "travel": "place",
                "fitness_and_medical": "event"}.get(cat, "purchase")
        merch = re.sub(r"^fraud_", "", str(r.merchant or "")).strip()
        city = str(r.city) if isinstance(r.city, str) else ""
        out.append(dict(
            id=f"c{i}", type=kind, t=r.ts.isoformat(timespec="minutes"),
            title=(merch or cat.replace("_", " "))[:60],
            note=(city or cat.replace("_", " "))[:90],
            category=cat.replace("_", " "),
            amount=round(float(r.amt), 2), mode="Credit Card",
            flow="Expense", source="card",
        ))
    return out


def music_receipts(sessions, budget=1400, keep_days=None):
    """Top sessions by significance, plus every session that shares a day with spend."""
    s = sessions.copy()
    s["day"] = s.start.dt.date.astype(str)
    must = s[s.day.isin(keep_days or set())].nlargest(700, "weight")
    rest = s.drop(must.index).nlargest(budget, "weight")
    picks = pd.concat([must, rest]).sort_values("start")

    out = []
    for _, r in picks.iterrows():
        if r.top_track_n >= 4:
            note = f"{r.top_track} × {r.top_track_n}"
        elif r.artist_share >= .8:
            note = f"{r.plays} tracks, all {r.top_artist}"
        else:
            note = f"{r.plays} tracks · {r.n_artists} artists"
        out.append(dict(
            id=f"m{int(r.sid)}", type="music",
            t=r.start.isoformat(timespec="minutes"),
            title=r.top_artist[:60], note=note[:90],
            category="music", amount=None, mode=r.platform, flow=None,
            minutes=float(r.minutes), plays=int(r.plays),
            loop=int(r.top_track_n), night=bool(r.night),
            track=r.top_track[:70], source="spotify",
        ))
    return out


# ─────────────────────────────────────────────────────────── threads ──

def build_threads(receipts):
    """Edges between receipts: same moment, same place, same artist echo."""
    by_id = {r["id"]: r for r in receipts}
    rs = sorted(receipts, key=lambda r: r["t"])
    ts = [pd.Timestamp(r["t"]) for r in rs]
    threads = []

    # 1. proximity — different kinds of receipt inside one window
    j = 0
    for i in range(len(rs)):
        j = max(j, i + 1)
        while j < len(rs) and (ts[j] - ts[i]).total_seconds() / 3600 <= MOMENT_WINDOW_H:
            a, b = rs[i], rs[j]
            if a["type"] != b["type"]:
                threads.append(dict(a=a["id"], b=b["id"], kind="moment",
                                    w=round(1 - (ts[j] - ts[i]).total_seconds()
                                            / (MOMENT_WINDOW_H * 3600), 2)))
            j += 1
        j = i + 1

    # 2. echo — same artist resurfacing after a long silence
    seen = defaultdict(list)
    for r in rs:
        if r["type"] == "music":
            seen[r["title"]].append(r)
    for artist, group in seen.items():
        for a, b in zip(group, group[1:]):
            gap = (pd.Timestamp(b["t"]) - pd.Timestamp(a["t"])).days
            if gap >= 365:
                threads.append(dict(a=a["id"], b=b["id"], kind="echo",
                                    w=min(1.0, gap / 1500), label=f"{gap} days later"))

    # 3. place — receipts naming the same location
    loc = defaultdict(list)
    for r in rs:
        m = PLACE_RE.search(r.get("note", "") or "")
        if m:
            loc[m.group(0).lower()].append(r)
    for place, group in loc.items():
        for a, b in zip(group, group[1:]):
            threads.append(dict(a=a["id"], b=b["id"], kind="place",
                                w=.5, label=place))

    return threads


# ─────────────────────────────────────────────────────────── moments ──

def build_moments(receipts, threads):
    """Connected components over 'moment' threads = one lived scene."""
    parent = {r["id"]: r["id"] for r in receipts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    size = {r["id"]: 1 for r in receipts}
    for t in sorted((x for x in threads if x["kind"] == "moment"),
                    key=lambda x: -x["w"]):
        ra, rb = find(t["a"]), find(t["b"])
        if ra != rb and size[ra] + size[rb] <= MOMENT_MAX:
            parent[ra] = rb
            size[rb] += size[ra]

    by_id = {r["id"]: r for r in receipts}
    groups = defaultdict(list)
    for rid in parent:
        groups[find(rid)].append(by_id[rid])

    moments = []
    for root, items in groups.items():
        kinds = {i["type"] for i in items}
        if len(items) < 3 or len(kinds) < 2:
            continue
        items.sort(key=lambda r: r["t"])
        start = pd.Timestamp(items[0]["t"])
        moments.append(dict(
            id=f"M{root}", t=items[0]["t"], n=len(items), kinds=sorted(kinds),
            members=[i["id"] for i in items][:14],
            spend=round(sum(i["amount"] or 0 for i in items), 2),
            narration=narrate(items, start),
        ))
    moments.sort(key=lambda m: (-m["n"], m["t"]))
    return moments[:220]


def narrate(items, start):
    """Plain-language reconstruction of a cluster of receipts."""
    day = start.strftime("%A, %d %B %Y")
    timed = [i for i in items if not i.get("dayonly")]
    if timed:
        h = pd.Timestamp(min(i["t"] for i in timed)).hour
        when = ("late that night" if h >= 23 or h < 5
                else "that morning" if h < 12
                else "that afternoon" if h < 17 else "that evening") + ", "
    else:
        when = ""

    music = [i for i in items if i["type"] == "music"]
    buys = [i for i in items if i["type"] in ("purchase", "media")
            and (i["amount"] or 0) > 0]
    places = [i for i in items if i["type"] == "place"]
    parts = []

    if music:
        m = max(music, key=lambda x: x.get("minutes", 0))
        if m.get("loop", 0) >= 4:
            parts.append(f"you played \u201c{m['track']}\u201d {m['loop']} times")
        elif m.get("minutes", 0) >= 20:
            parts.append(f"you gave {int(m['minutes'])} minutes to {m['title']}")
        else:
            parts.append(f"you put on {m['title']}")
    if places:
        parts.append("you were moving \u2014 " + tidy_place(places[0]))
    if buys:
        small = [b for b in buys if (b["amount"] or 0) < 5000]
        lead = max(buys, key=lambda b: b["amount"])
        names = ", ".join(dict.fromkeys(b["title"].lower() for b in small[:3]))
        if names and lead["amount"] < 5000:
            parts.append(f"you spent \u20b9{sum(b['amount'] for b in small):,.0f} "
                         f"on {names}")
        else:
            parts.append(f"\u20b9{lead['amount']:,.0f} went on "
                         f"{lead['title'].lower()}")

    body = "; then ".join(parts) if parts else "several things happened at once"
    return f"{day} \u2014 {when}{body}."


def tidy_place(r):
    return (r.get("note") or r["title"]).strip(" .-") or "somewhere"


# ────────────────────────────────────────────────────────── chapters ──

def build_chapters(music, sessions, spend):
    """Segment the timeline where behaviour actually changes."""
    m = music.copy()
    m["mo"] = m.ts.dt.to_period("M")
    feat = m.groupby("mo").agg(
        plays=("ts", "size"),
        hours=("min_played", lambda x: x.sum() / 60),
        night=("ts", lambda x: ((x.dt.hour >= 23) | (x.dt.hour < 5)).mean()),
        skip=("skipped", "mean"),
        variety=("artist_name", "nunique"),
    )
    feat = feat.reindex(pd.period_range(feat.index.min(), feat.index.max(), freq="M"),
                        fill_value=0)

    X = feat[["hours", "night", "skip", "variety"]].to_numpy(float)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)

    spend = spend.copy()
    spend["mo"] = spend.ts.dt.to_period("M")

    bounds = [0] + binary_segment(X, depth=3) + [len(feat)]

    # Two neighbouring stretches that describe the same behaviour are one
    # stretch; label, look for repeats, merge, label again.
    for _ in range(4):
        chapters = label_chapters(chapters_from(bounds, feat, m, spend))
        dupes = [i for i in range(1, len(chapters))
                 if chapters[i]["base"] == chapters[i - 1]["base"]]
        if not dupes:
            return chapters
        bounds.pop(dupes[0])
    return chapters


def chapters_from(bounds, feat, m, spend):
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 2:
            continue
        window = feat.index[a:b]
        sl = feat.iloc[a:b]
        mus = m[m.mo.isin(window)]
        sp = spend[spend.mo.isin(window)]
        top = mus.artist_name.value_counts().head(5)
        out.append(dict(
            start=str(window[0]), end=str(window[-1]),
            months=int(b - a),
            hours=round(float(sl.hours.sum()), 1),
            plays=int(sl.plays.sum()),
            variety=int(mus.artist_name.nunique()),
            night=round(float(sl.night.mean()), 2),
            skip=round(float(sl.skip.mean()), 2),
            artists=[{"name": k, "n": int(v)} for k, v in top.items()],
            spend=round(float(sp[sp["Income/Expense"] == "Expense"].Amount.sum()), 0),
            top_spend=[{"name": k, "n": int(v)} for k, v in
                       sp.Category.value_counts().head(4).items()],
            title="", sketch="",
        ))
    return out


def binary_segment(X, depth=3, min_len=4):
    """Recursive change-point search — split where the two halves differ most."""
    cuts = []

    def split(lo, hi, d):
        if d == 0 or hi - lo < 2 * min_len:
            return
        best, best_k = -1, None
        for k in range(lo + min_len, hi - min_len):
            gain = np.linalg.norm(X[lo:k].mean(0) - X[k:hi].mean(0))
            gain *= min(k - lo, hi - k) ** .5
            if gain > best:
                best, best_k = gain, k
        if best_k is None:
            return
        cuts.append(best_k)
        split(lo, best_k, d - 1)
        split(best_k, hi, d - 1)

    split(0, len(X), depth)
    return sorted(cuts)


def label_chapters(chapters):
    """Name each chapter from its own numbers, relative to the whole life."""
    hpm = np.array([c["hours"] / max(c["months"], 1) for c in chapters])
    var = np.array([c["variety"] / max(c["plays"], 1) for c in chapters])
    used = set()

    for i, c in enumerate(chapters):
        rate = hpm[i]
        prev = chapters[i - 1] if i else None
        cands = []
        if prev and prev["skip"] - c["skip"] > .3:
            cands.append((3.2, "The Turn",
                f"Skipping collapsed from {prev['skip']:.0%} to {c['skip']:.0%}. "
                f"Whatever you'd been looking for, you found it here \u2014 and it "
                f"sounded like {c['artists'][0]['name']}."))
        if (prev and c["skip"] - prev["skip"] > .08
                and c["plays"] > 2000 and prev["plays"] > 3000):
            cands.append((3.0, "The Drift Back",
                f"After years of finishing almost everything you started, skips "
                f"climbed back to {c['skip']:.0%} across {c['plays']:,} plays. "
                f"Restlessness, returning late."))
        if c["skip"] > .45:
            cands.append((c["skip"] * 3, "The Restless Stretch",
                f"You skipped {c['skip']:.0%} of everything you started. "
                f"{c['plays']:,} plays, almost none of them finished \u2014 "
                f"someone hunting for a song they couldn't name."))
        if rate >= hpm.max() * .95:
            cands.append((2.6, "The Loudest It Got",
                f"Your heaviest listening of the whole decade: "
                f"{c['hours']:,.0f} hours in {c['months']} months, about "
                f"{rate / 30:.1f} hours every single day."))
        if c["night"] > .42:
            cands.append((c["night"] * 2.4, "The Night Shift",
                f"{c['night']:.0%} of this chapter happened after 11pm. "
                f"{c['artists'][0]['name']} kept you company for most of it."))
        if c["plays"] < 400:
            cands.append((2.2, "The Quiet Stretch",
                f"Only {c['plays']:,} plays across {c['months']} months. "
                f"Whatever you were doing, you weren't doing it to music."))
        if var[i] >= var.max() * .9 and c["variety"] >= 250:
            cands.append((2.0, "The Wide Net",
                f"{c['variety']:,} different artists across {c['months']} months, "
                f"barely repeating yourself \u2014 the widest your taste ever ran."))
        if c["artists"] and c["artists"][0]["n"] / max(c["plays"], 1) > .12:
            cands.append((1.9, f"The {c['artists'][0]['name'].removeprefix('The ')} Years",
                f"One name took over: {c['artists'][0]['n']:,} of your "
                f"{c['plays']:,} plays. Everything else orbited it."))
        if rate > 60:
            cands.append((1.5, "The Deep End",
                f"{c['hours']:,.0f} hours in {c['months']} months. Music stopped "
                f"being background and became the room you lived in."))
        cands.append((0.5, "Finding the Signal",
            f"{c['hours']:,.0f} hours, settling into "
            f"{c['artists'][0]['name'] if c['artists'] else 'silence'}. Skips at "
            f"{c['skip']:.0%} \u2014 you'd stopped searching and started listening."))

        ranked = sorted(cands, key=lambda x: -x[0])
        c["base"] = ranked[0][1]
        for _, title, sketch in ranked:
            if title not in used:
                used.add(title)
                break
        else:
            title, sketch = ranked[0][1], ranked[0][2]
        c["title"], c["sketch"] = title, sketch
        if c["spend"] > 0 and c["top_spend"]:
            c["sketch"] += (f" Recorded spending leaned on "
                            f"{c['top_spend'][0]['name'].lower()} "
                            f"(\u20b9{c['spend']:,.0f} logged).")
    return chapters


# ────────────────────────────────────────────────────────── patterns ──

def build_patterns(music, sessions, spend, card):
    m = music.copy()
    m["mo"] = m.ts.dt.to_period("M")

    # ghosts — artists that burned bright, then disappeared
    g = m.groupby("artist_name").agg(n=("ts", "size"), first=("ts", "min"),
                                     last=("ts", "max"))
    g = g[g.n >= 50]
    pm = m.groupby(["artist_name", "mo"]).size().rename("c").reset_index()
    pk = pm.loc[pm.groupby("artist_name").c.idxmax()].set_index("artist_name")
    g = g.join(pk[["mo", "c"]].rename(columns={"mo": "peak", "c": "peak_n"}))
    g["focus"] = g.peak_n / g.n
    ghosts = g[(g.last < m.ts.max() - pd.Timedelta(days=300)) & (g.focus > .3)]
    ghosts = ghosts.nlargest(8, "peak_n")

    # loops — one track on repeat inside a single session
    loops = sessions.nlargest(10, "top_track_n")

    # monthly series for the pulse chart
    series = m.groupby("mo").agg(
        plays=("ts", "size"),
        hours=("min_played", lambda x: round(x.sum() / 60, 1)),
        night=("ts", lambda x: round(((x.dt.hour >= 23) | (x.dt.hour < 5)).mean(), 2)),
    ).reindex(pd.period_range(m.mo.min(), m.mo.max(), freq="M"), fill_value=0)

    sp = spend[spend["Income/Expense"] == "Expense"].copy()
    sp["mo"] = sp.ts.dt.to_period("M")
    spend_series = sp.groupby("mo").Amount.sum().round(0)

    clock = m.ts.dt.hour.value_counts().reindex(range(24), fill_value=0)

    return dict(
        ghosts=[dict(name=i, plays=int(r.n), peak=str(r.peak), peak_n=int(r.peak_n),
                     first=r.first.strftime("%b %Y"), last=r.last.strftime("%b %Y"),
                     focus=round(float(r.focus), 2))
                for i, r in ghosts.iterrows()],
        loops=[dict(track=r.top_track, artist=r.top_artist, n=int(r.top_track_n),
                    t=r.start.isoformat(timespec="minutes"),
                    hour=int(r.hour), minutes=float(r.minutes))
               for _, r in loops.iterrows()],
        series=[dict(m=str(i), plays=int(r.plays), hours=float(r.hours),
                     night=float(r.night),
                     spend=float(spend_series.get(i, 0)))
                for i, r in series.iterrows()],
        clock=[int(v) for v in clock],
        vocab=dict(
            artists=int(m.artist_name.nunique()),
            tracks=int(m.track_name.nunique()),
            hours=round(float(m.min_played.sum() / 60)),
            plays=int(len(m)),
            spends=int(len(spend)) + int(len(card)),
        ),
    )


# ─────────────────────────────────────────────────────────────── main ──

def main():
    music, spend, card = load_music(), load_spend(), load_card()
    sessions = build_sessions(music)

    spend_days = set(spend.ts.dt.date.astype(str))
    receipts = (spend_receipts(spend)
                + card_receipts(card)
                + music_receipts(sessions, keep_days=spend_days))
    receipts.sort(key=lambda r: r["t"])

    threads = build_threads(receipts)
    moments = build_moments(receipts, threads)
    chapters = build_chapters(music, sessions, spend)
    patterns = build_patterns(music, sessions, spend, card)

    keep = {r for m in moments for r in m["members"]}
    threads = [t for t in threads
               if t["kind"] != "moment" or (t["a"] in keep and t["b"] in keep)]

    bundle = dict(
        meta=dict(
            span=[receipts[0]["t"][:10], receipts[-1]["t"][:10]],
            receipts=len(receipts), threads=len(threads),
            moments=len(moments), chapters=len(chapters),
        ),
        receipts=receipts, threads=threads, moments=moments,
        chapters=chapters, patterns=patterns,
    )
    with open(OUT, "w") as f:
        json.dump(bundle, f, separators=(",", ":"))
    print(f"wrote {OUT}")
    print({k: (len(v) if isinstance(v, list) else v)
           for k, v in bundle.items() if k != "patterns"})


if __name__ == "__main__":
    main()
