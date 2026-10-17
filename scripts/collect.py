#!/usr/bin/env python3
"""data/config.json の sources から漏えい関連の候補記事を集め、data/raw/YYYY-MM-DD.json に書き出す。

使い方:
  python3 scripts/collect.py [--hours 36] [--dry-run] [YYYY-MM-DD]

標準ライブラリのみ。取得できなかったソースは source_status に "failed" と記録して続行する。
出力はルーチンが読んで事案を作る / 続報を足すための下書きで、サイトはこのファイルを読まない（.gitignore 済み）。

候補には次のヒントを付ける（最終判断はルーチン）:
  leak_score        漏えいキーワードのヒット数。0 のものは候補に入れない
  seen              data/state/seen.json にある URL（処理済み）なら true
  maybe_update_of   data/orgs.json の組織名・別名がタイトルに含まれ、公表日が近い既存事案の id
--dry-run は取得と表示だけ行い、data/raw/ に書かない。
"""
import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "config.json"
RAW_DIR = ROOT / "data" / "raw"
INDEX = ROOT / "data" / "index.json"
ORGS = ROOT / "data" / "orgs.json"
SEEN = ROOT / "data" / "state" / "seen.json"
JST = timezone(timedelta(hours=9))
UA = "data-leak-search/0.1 (+https://github.com/ke-iwata/data-leak-search)"


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def strip_tags(s, limit=400):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(raw):
    """RSS 2.0 / RSS 1.0 (RDF) / Atom を [{title, url, published, summary}] にする"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        text = raw.decode("utf-8", "replace")
        text = re.sub(r"&(?!#?\w+;)", "&amp;", text)
        root = ET.fromstring(text.encode("utf-8"))
    out = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        rec = {"title": "", "url": "", "published": None, "summary": ""}
        for c in el:
            n = local(c.tag)
            if n == "title":
                rec["title"] = strip_tags(c.text or "", 300)
            elif n == "link":
                href = c.get("href")
                if href and c.get("rel", "alternate") == "alternate":
                    rec["url"] = href
                elif not href and c.text:
                    rec["url"] = c.text.strip()
            elif n in ("pubDate", "published", "updated", "date") and not rec["published"]:
                rec["published"] = parse_date(c.text)
            elif n in ("description", "summary", "content", "encoded") and not rec["summary"]:
                rec["summary"] = strip_tags(c.text or "")
        if rec["title"] and rec["url"]:
            out.append(rec)
    return out


def keyword_hits(text, keywords):
    """英数字のキーワードは単語境界で照合する（"leak" が "leaky" に当たらないように）"""
    t = text.lower()
    hits = []
    for k in keywords:
        k2 = k.lower()
        if re.fullmatch(r"[a-z0-9 .+-]+", k2):
            pat = r"(?<![a-z0-9])" + re.escape(k2) + r"(?![a-z0-9])"
        else:
            pat = re.escape(k2)
        if re.search(pat, t):
            hits.append(k)
    return hits


def collect_rss(src, since):
    items = []
    for e in parse_feed(fetch(src["url"])):
        if e["published"] and e["published"] < since:
            continue
        items.append({
            "title": e["title"], "url": e["url"], "summary": e["summary"],
            "published_at": e["published"].astimezone(JST).isoformat(timespec="seconds") if e["published"] else None,
        })
    return items


TIME_LINK_RE = re.compile(
    r'<time[^>]*datetime="\s*(\d{4}-\d{2}-\d{2})\s*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S,
)


def collect_html(src, since):
    """一覧ページから <time datetime> と、その直後の <a> を 1 件として拾う（個人情報保護委員会など）"""
    if src.get("parser", "time_link") != "time_link":
        raise ValueError(f"unknown parser: {src.get('parser')}")
    page = fetch(src["url"]).decode("utf-8", "replace")
    items = []
    for m in TIME_LINK_RE.finditer(page):
        d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=JST)
        if d < since.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        items.append({
            "title": strip_tags(m.group(3), 300),
            "url": urllib.parse.urljoin(src["url"], html.unescape(m.group(2))),
            "summary": "",
            "published_at": d.isoformat(timespec="seconds"),
        })
    return items


def collect_hibp(src, since):
    """HIBP の breaches API。AddedDate が期間内のものだけ。海外事案は業種・原因の推定になるため confidence を下げる前提"""
    data = json.loads(fetch(src["url"]))
    items = []
    for b in data:
        added = parse_date(b.get("AddedDate"))
        if not added or added < since:
            continue
        items.append({
            "title": f"{b.get('Title') or b.get('Name')} ({b.get('Domain') or '-'}) — {b.get('PwnCount', 0):,} accounts",
            "url": f"https://haveibeenpwned.com/Breach/{b.get('Name')}",
            "summary": strip_tags(b.get("Description", "")),
            "published_at": added.astimezone(JST).isoformat(timespec="seconds"),
            "hibp": {
                "name": b.get("Name"), "domain": b.get("Domain"), "breach_date": b.get("BreachDate"),
                "pwn_count": b.get("PwnCount"), "data_classes": b.get("DataClasses", []),
                "is_verified": b.get("IsVerified"), "is_sensitive": b.get("IsSensitive"),
                "is_spam_list": b.get("IsSpamList"), "is_stealer_log": b.get("IsStealerLog"),
                "disclosure_url": b.get("DisclosureUrl"),
            },
        })
    return items


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def org_matcher(index, orgs, cfg):
    """候補タイトルに組織名・別名が含まれ、公表日が window 内の既存事案 id を返す関数を作る"""
    window = timedelta(days=cfg.get("matching", {}).get("update_window_days", 30))
    min_len = cfg.get("matching", {}).get("min_org_name_len", 2)
    names = []  # (name, slug)
    for slug, o in orgs.items():
        for n in [o.get("name", "")] + list(o.get("aliases", [])):
            if n and len(n) >= min_len:
                names.append((n, slug))
    by_org = {}
    for rec in index.get("incidents", []):
        by_org.setdefault(rec["org"], []).append(rec)

    def match(title, published_at):
        hits = []
        t = title.lower()
        for n, slug in names:
            if n.lower() in t:
                for rec in by_org.get(slug, []):
                    if not published_at or not rec.get("disclosed_at"):
                        hits.append(rec["id"])
                        continue
                    try:
                        dd = date.fromisoformat(rec["disclosed_at"])
                        pd = date.fromisoformat(published_at[:10])
                    except ValueError:
                        hits.append(rec["id"])
                        continue
                    if abs((pd - dd).days) <= window.days or rec.get("status") in ("investigating", "disclosed"):
                        hits.append(rec["id"])
        return sorted(set(hits))

    return match


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?")
    ap.add_argument("--hours", type=int, default=36, help="何時間前までの記事を集めるか")
    ap.add_argument("--dry-run", action="store_true", help="data/raw/ に書かず、結果を表示するだけ")
    args = ap.parse_args()

    now = datetime.now(JST)
    day = args.date or now.strftime("%Y-%m-%d")
    since = now - timedelta(hours=args.hours)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    leak_kw, excl_kw = cfg["leak_keywords"], cfg.get("exclude_keywords", [])
    seen = load_json(SEEN, {})
    match = org_matcher(load_json(INDEX, {}), load_json(ORGS, {}), cfg)

    status, cands, dropped, urls = {}, [], 0, set()
    for src in cfg["sources"]:
        try:
            if src["kind"] == "rss":
                got = collect_rss(src, since)
            elif src["kind"] == "html":
                got = collect_html(src, since)
            elif src["kind"] == "hibp":
                got = collect_hibp(src, since)
            else:
                raise ValueError(f"unknown kind: {src['kind']}")
            status[src["id"]] = {"status": "ok", "fetched": len(got)}
        except Exception as e:  # noqa: BLE001 — どのソースが落ちても他は続ける
            status[src["id"]] = {"status": "failed", "error": str(e)[:200]}
            print(f"[failed] {src['id']}: {e}", file=sys.stderr)
            continue
        kept = 0
        for it in got:
            key = it["url"].split("#")[0].rstrip("/")
            if key in urls:
                continue
            urls.add(key)
            text = it["title"] + " " + it.get("summary", "")
            hits = keyword_hits(text, leak_kw) if src["kind"] != "hibp" else ["hibp"]
            if not hits or keyword_hits(it["title"], excl_kw):
                dropped += 1
                continue
            it.update({
                "source": src["name"], "source_id": src["id"], "trust": src["trust"],
                "leak_score": len(hits), "matched_keywords": hits,
                "seen": key in seen or it["url"] in seen,
                "maybe_update_of": match(it["title"], it.get("published_at")),
            })
            cands.append(it)
            kept += 1
        status[src["id"]]["candidates"] = kept

    # 既存事案に紐づきそうなもの → 未処理 → スコア順
    cands.sort(key=lambda c: (bool(c["maybe_update_of"]), not c["seen"], c["leak_score"], c.get("published_at") or ""), reverse=True)
    out = {
        "date": day, "collected_at": now.isoformat(timespec="seconds"), "since": since.isoformat(timespec="seconds"),
        "source_status": status, "count": len(cands), "dropped": dropped, "candidates": cands,
    }
    ok = sum(1 for s in status.values() if s["status"] == "ok")
    if args.dry_run:
        for c in cands[:40]:
            flag = "U" if c["maybe_update_of"] else ("s" if c["seen"] else "·")
            print(f"{flag} [{c['source_id']}] {c['title'][:80]}  {c.get('published_at', '')[:10]}  {c['url']}")
        print(f"(dry-run) {len(cands)} candidates, {dropped} dropped, sources ok {ok}/{len(status)}")
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{day}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{path.relative_to(ROOT)}: {len(cands)} candidates, {dropped} dropped, sources ok {ok}/{len(status)}")


if __name__ == "__main__":
    main()
