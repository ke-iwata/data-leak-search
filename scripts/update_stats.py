#!/usr/bin/env python3
"""data/incidents/*.json と data/daily/*.json を検証し、サイト用の生成ファイルを作り直す。

使い方:
  python3 scripts/update_stats.py              検証＋生成
  python3 scripts/update_stats.py --check-only 検証のみ（ファイルは変更しない）
エラーがあれば非ゼロで終了する。

生成するもの（手で書かない）:
  data/index.json        全事案の軽量インデックス（一覧・検索用）
  data/stats.json        月次・原因別・業種別・データ種別・規模分布・上位・日次件数などの集計
  data/orgs.json         組織 slug → 名前・別名・業種・事案 id 一覧
  data/state/seen.json   処理済み URL（事案の出典 URL + daily の skipped URL）。collect.py が読む
"""
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "config.json"
INCIDENTS = ROOT / "data" / "incidents"
DAILY = ROOT / "data" / "daily"
INDEX = ROOT / "data" / "index.json"
STATS = ROOT / "data" / "stats.json"
ORGS = ROOT / "data" / "orgs.json"
SEEN = ROOT / "data" / "state" / "seen.json"
JST = timezone(timedelta(hours=9))

ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
KEYWORD_RE = re.compile(r"^[a-z0-9][a-z0-9.+#/-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
# 個人情報の混入チェック。メールアドレスと 14〜16 桁の数字（カード番号らしきもの）を本文に書いたら検証エラー
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,15}\d(?!\d)")
SCALE_BUCKETS = [("〜1千", 1_000), ("〜1万", 10_000), ("〜10万", 100_000), ("〜100万", 1_000_000), ("100万〜", None)]
MONTHS_KEPT = 36
HEATMAP_DAYS = 91
RECENT_DAYS = 30


def is_url(u):
    return isinstance(u, str) and u.startswith(("https://", "http://"))


def is_date(s):
    if not isinstance(s, str) or not DATE_RE.match(s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def pii_check(errors, where, text):
    if not isinstance(text, str):
        return
    if EMAIL_RE.search(text):
        errors.append(f"{where}: メールアドレスらしき文字列があります。個人情報は書かない")
    if CARD_RE.search(text):
        errors.append(f"{where}: カード番号らしき 14〜16 桁の数字があります")


def validate_incident(errors, where, inc, cfg, month):
    causes, industries = {c["id"] for c in cfg["causes"]}, {i["id"] for i in cfg["industries"]}
    dtypes, statuses, kinds = {d["id"] for d in cfg["data_types"]}, set(cfg["statuses"]), set(cfg["timeline_kinds"])
    req = ("id", "org", "org_name", "industry", "country", "title", "summary", "cause", "data_types",
           "disclosed_at", "status", "confidence", "sources", "timeline", "created_at", "updated_at")
    for k in req:
        if k not in inc:
            errors.append(f"{where}: '{k}' がありません")
    if errors and any(e.startswith(where) for e in errors[-len(req):]):
        return
    if not ID_RE.match(inc["id"]):
        errors.append(f"{where}: id は YYYY-MM-DD-slug 形式")
    elif inc["id"][:10] != inc["disclosed_at"]:
        errors.append(f"{where}: id の日付 {inc['id'][:10]} と disclosed_at {inc['disclosed_at']} が違います")
    if not SLUG_RE.match(inc["org"]):
        errors.append(f"{where}: org は小文字英数字とハイフンの slug")
    if inc["industry"] not in industries:
        errors.append(f"{where}: industry '{inc['industry']}' は {sorted(industries)} のいずれか")
    if inc["cause"] not in causes:
        errors.append(f"{where}: cause '{inc['cause']}' は {sorted(causes)} のいずれか")
    if inc["status"] not in statuses:
        errors.append(f"{where}: status は {sorted(statuses)} のいずれか")
    if not isinstance(inc["country"], str) or not re.fullmatch(r"[A-Z]{2}", inc["country"]):
        errors.append(f"{where}: country は ISO 3166-1 の 2 文字（JP など）")
    for d in inc["data_types"]:
        if d not in dtypes:
            errors.append(f"{where}: data_type '{d}' が不正です")
    if not is_date(inc["disclosed_at"]):
        errors.append(f"{where}: disclosed_at は YYYY-MM-DD")
    elif inc["disclosed_at"][:7] != month:
        errors.append(f"{where}: disclosed_at の月 {inc['disclosed_at'][:7]} がファイルの月 {month} と違います")
    for k in ("occurred_at", "detected_at", "created_at", "updated_at"):
        v = inc.get(k)
        if v is not None and not is_date(v):
            errors.append(f"{where}: {k} は YYYY-MM-DD か null")
    aff = inc.get("affected")
    if aff is not None and (not isinstance(aff, int) or isinstance(aff, bool) or aff < 0):
        errors.append(f"{where}: affected は 0 以上の整数か null")
    if not isinstance(inc.get("affected_is_estimate", False), bool):
        errors.append(f"{where}: affected_is_estimate は true / false")
    c = inc["confidence"]
    if not isinstance(c, (int, float)) or isinstance(c, bool) or not 0 <= c <= 1:
        errors.append(f"{where}: confidence は 0〜1")
    if not inc["sources"]:
        errors.append(f"{where}: sources は 1 件以上（出典の無い事案は載せない）")
    for i, s in enumerate(inc["sources"]):
        if s.get("role") not in ("primary", "secondary"):
            errors.append(f"{where} sources[{i}]: role は primary / secondary")
        if s.get("trust") not in ("official", "authority", "media", "aggregator"):
            errors.append(f"{where} sources[{i}]: trust は official / authority / media / aggregator")
        if not is_url(s.get("url")):
            errors.append(f"{where} sources[{i}]: url は http(s) の URL")
        if not s.get("publisher"):
            errors.append(f"{where} sources[{i}]: publisher がありません")
        if s.get("published_at") is not None and not is_date(s.get("published_at")):
            errors.append(f"{where} sources[{i}]: published_at は YYYY-MM-DD か null")
    for i, t in enumerate(inc["timeline"]):
        if t.get("kind") not in kinds:
            errors.append(f"{where} timeline[{i}]: kind は {sorted(kinds)} のいずれか")
        if t.get("date") is not None and not is_date(t.get("date")):
            errors.append(f"{where} timeline[{i}]: date は YYYY-MM-DD か null")
        if not t.get("text"):
            errors.append(f"{where} timeline[{i}]: text がありません")
        src = t.get("source")
        if src is not None and (not isinstance(src, int) or not 0 <= src < len(inc["sources"])):
            errors.append(f"{where} timeline[{i}]: source は sources のインデックスか null")
    for kw in inc.get("keywords", []):
        if not KEYWORD_RE.match(kw):
            errors.append(f"{where}: keyword '{kw}' は小文字・数字・ハイフンの slug にする")
    for k in ("title", "summary", "vector", "org_name"):
        pii_check(errors, f"{where} {k}", inc.get(k))
    for i, t in enumerate(inc["timeline"]):
        pii_check(errors, f"{where} timeline[{i}]", t.get("text"))


def validate_daily(errors, path, d, ids):
    w = path.name
    if d.get("date") != path.stem:
        errors.append(f"{w}: date が {path.stem} ではありません")
    for k in ("generated_at", "stats", "digest", "new", "updated"):
        if k not in d:
            errors.append(f"{w}: '{k}' がありません")
    if any(e.startswith(w) for e in errors):
        return
    if not isinstance(d["digest"], list) or len(d["digest"]) > 5:
        errors.append(f"{w}: digest は 0〜5 件")
    for i, g in enumerate(d["digest"]):
        if not g.get("text"):
            errors.append(f"{w} digest[{i}]: text がありません")
        for iid in g.get("incidents", []):
            if iid not in ids:
                errors.append(f"{w} digest[{i}]: 事案 {iid} がありません")
        pii_check(errors, f"{w} digest[{i}]", g.get("text"))
    for iid in d["new"]:
        if iid not in ids:
            errors.append(f"{w} new: 事案 {iid} がありません")
    for i, u in enumerate(d["updated"]):
        if not isinstance(u, dict) or u.get("id") not in ids or not u.get("text"):
            errors.append(f"{w} updated[{i}]: {{id, text}} で、id は既存の事案")
    for i, s in enumerate(d.get("skipped", [])):
        if not is_url(s.get("url")) or not s.get("reason"):
            errors.append(f"{w} skipped[{i}]: {{url, reason}}")


def load_all(cfg):
    errors, incidents, dailies = [], {}, {}
    INCIDENTS.mkdir(parents=True, exist_ok=True)
    DAILY.mkdir(parents=True, exist_ok=True)
    for p in sorted(INCIDENTS.glob("*.json")):
        if not MONTH_RE.match(p.stem):
            errors.append(f"{p.name}: ファイル名は YYYY-MM.json")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as ex:
            errors.append(f"{p.name}: JSON が壊れています ({ex})")
            continue
        if d.get("month") != p.stem or not isinstance(d.get("incidents"), list):
            errors.append(f"{p.name}: {{month: '{p.stem}', incidents: [...]}} の形にする")
            continue
        for i, inc in enumerate(d["incidents"]):
            where = f"{p.name} incidents[{i}] ({inc.get('id')})"
            validate_incident(errors, where, inc, cfg, p.stem)
            if inc.get("id") in incidents:
                errors.append(f"{where}: id が重複しています")
            incidents[inc.get("id")] = inc
    for p in sorted(DAILY.glob("*.json")):
        if not DATE_RE.match(p.stem):
            errors.append(f"{p.name}: ファイル名は YYYY-MM-DD.json")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as ex:
            errors.append(f"{p.name}: JSON が壊れています ({ex})")
            continue
        validate_daily(errors, p, d, incidents)
        dailies[p.stem] = d
    return errors, incidents, dailies


def month_iter(end_month, n):
    y, m = int(end_month[:4]), int(end_month[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def bucket_of(aff):
    for label, upper in SCALE_BUCKETS:
        if upper is None or aff < upper:
            return label
    return SCALE_BUCKETS[-1][0]


def build(cfg, incidents, dailies, today):
    incs = sorted(incidents.values(), key=lambda i: (i["disclosed_at"], i["id"]), reverse=True)
    cause_label = {c["id"]: c["label"] for c in cfg["causes"]}

    # ---- index.json
    index = {
        "generated_at": today.isoformat(timespec="seconds"),
        "months": sorted({i["disclosed_at"][:7] for i in incs}, reverse=True),
        "count": len(incs),
        "incidents": [{
            "id": i["id"], "org": i["org"], "org_name": i["org_name"], "org_aliases": i.get("org_aliases", []),
            "industry": i["industry"], "country": i["country"], "title": i["title"], "summary": i["summary"],
            "cause": i["cause"], "data_types": i["data_types"], "affected": i.get("affected"),
            "affected_is_estimate": bool(i.get("affected_is_estimate", False)),
            "disclosed_at": i["disclosed_at"], "updated_at": i["updated_at"], "status": i["status"],
            "confidence": i["confidence"], "month": i["disclosed_at"][:7],
            "n_sources": len(i["sources"]), "has_primary": any(s.get("role") == "primary" for s in i["sources"]),
            "keywords": i.get("keywords", []),
        } for i in incs],
    }

    # ---- orgs.json
    orgs = {}
    for i in sorted(incs, key=lambda x: x["disclosed_at"]):
        o = orgs.setdefault(i["org"], {"name": i["org_name"], "aliases": [], "industry": i["industry"], "country": i["country"],
                                       "incidents": [], "first": i["disclosed_at"], "last": i["disclosed_at"], "affected_total": 0})
        o["name"] = i["org_name"]  # 新しい事案の表記を採用
        for a in i.get("org_aliases", []):
            if a not in o["aliases"] and a != o["name"]:
                o["aliases"].append(a)
        o["incidents"].append(i["id"])
        o["last"] = i["disclosed_at"]
        o["affected_total"] += i.get("affected") or 0
    for o in orgs.values():
        o["incidents"].sort(reverse=True)

    # ---- stats.json
    cur_month = today.strftime("%Y-%m")
    months = month_iter(cur_month, MONTHS_KEPT)
    by_month = {m: {"month": m, "count": 0, "affected": 0, "by_cause": Counter(), "by_industry": Counter()} for m in months}
    for i in incs:
        m = i["disclosed_at"][:7]
        if m in by_month:
            b = by_month[m]
            b["count"] += 1
            b["affected"] += i.get("affected") or 0
            b["by_cause"][i["cause"]] += 1
            b["by_industry"][i["industry"]] += 1
    monthly = [{**b, "by_cause": dict(b["by_cause"]), "by_industry": dict(b["by_industry"]), "partial": b["month"] == cur_month} for b in by_month.values()]

    one_year_ago = (today.date() - timedelta(days=365)).isoformat()
    last_year = [i for i in incs if i["disclosed_at"] >= one_year_ago]
    known = [i["affected"] for i in last_year if isinstance(i.get("affected"), int)]
    days_to_disclose = []
    for i in last_year:
        if i.get("occurred_at") and is_date(i["occurred_at"]):
            delta = (date.fromisoformat(i["disclosed_at"]) - date.fromisoformat(i["occurred_at"])).days
            if 0 <= delta <= 3650:
                days_to_disclose.append(delta)
    totals = {
        "period": {"from": one_year_ago, "to": today.date().isoformat()},
        "incidents": len(last_year),
        "affected_sum": sum(known),
        "affected_known": len(known),
        "affected_unknown": len(last_year) - len(known),
        "affected_median": int(statistics.median(known)) if known else None,
        "days_to_disclose_median": int(statistics.median(days_to_disclose)) if days_to_disclose else None,
    }
    by_cause = Counter(i["cause"] for i in last_year)
    affected_by_cause = Counter()
    for i in last_year:
        affected_by_cause[i["cause"]] += i.get("affected") or 0
    by_industry = Counter(i["industry"] for i in last_year)
    by_data_type = Counter(d for i in last_year for d in set(i["data_types"]))
    scale = Counter(bucket_of(a) for a in known)
    top = sorted((i for i in last_year if isinstance(i.get("affected"), int)), key=lambda i: -i["affected"])[:10]

    # 日次件数（公表日ベース）: ヒートマップ用に直近 91 日
    day_axis = [(today.date() - timedelta(days=HEATMAP_DAYS - 1 - k)).isoformat() for k in range(HEATMAP_DAYS)]
    per_day = Counter(i["disclosed_at"] for i in incs)
    daily_counts = [{"date": d, "count": per_day.get(d, 0)} for d in day_axis]

    # ルーチンの実績: 直近 30 日の daily ファイルから新規・続報の件数
    recent_axis = [(today.date() - timedelta(days=RECENT_DAYS - 1 - k)).isoformat() for k in range(RECENT_DAYS)]
    recent_days = []
    for d in recent_axis:
        dd = dailies.get(d)
        recent_days.append({"date": d, "new": len(dd["new"]) if dd else None, "updated": len(dd["updated"]) if dd else None,
                            "affected_new": sum(incidents[x].get("affected") or 0 for x in dd["new"]) if dd else None})

    stats = {
        "generated_at": today.isoformat(timespec="seconds"),
        "latest_daily": max(dailies) if dailies else None,
        "dates": sorted(dailies, reverse=True),
        "total_incidents": len(incs),
        "totals_1y": totals,
        "monthly": monthly,
        "by_cause": [{"id": c, "label": cause_label[c], "count": by_cause.get(c, 0), "affected": affected_by_cause.get(c, 0)} for c in cause_label],
        "by_industry": [{"id": i["id"], "label": i["label"], "count": by_industry.get(i["id"], 0)} for i in cfg["industries"]],
        "by_data_type": [{"id": d["id"], "label": d["label"], "count": by_data_type.get(d["id"], 0),
                          "pct": round(by_data_type.get(d["id"], 0) / len(last_year) * 100) if last_year else 0} for d in cfg["data_types"]],
        "scale_buckets": [{"label": label, "count": scale.get(label, 0)} for label, _ in SCALE_BUCKETS],
        "top_affected": [{"id": i["id"], "org": i["org"], "org_name": i["org_name"], "industry": i["industry"], "cause": i["cause"],
                          "disclosed_at": i["disclosed_at"], "affected": i["affected"], "affected_is_estimate": bool(i.get("affected_is_estimate", False))} for i in top],
        "daily_counts": daily_counts,
        "recent_days": recent_days,
    }

    # ---- seen.json
    seen = {}
    for i in incs:
        for s in i["sources"]:
            seen[s["url"].split("#")[0].rstrip("/")] = {"incident": i["id"], "at": s.get("published_at") or i["disclosed_at"]}
    for d, dd in dailies.items():
        for s in dd.get("skipped", []):
            seen.setdefault(s["url"].split("#")[0].rstrip("/"), {"skipped": s["reason"][:80], "at": d})
    return index, stats, orgs, seen


def main():
    check_only = "--check-only" in sys.argv
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    errors, incidents, dailies = load_all(cfg)
    if errors:
        print("\n".join("ERROR " + x for x in errors), file=sys.stderr)
        sys.exit(1)
    today = datetime.now(JST)
    index, stats, orgs, seen = build(cfg, incidents, dailies, today)
    if check_only:
        print(f"ok: {len(incidents)} incidents, {len(dailies)} daily files, {len(orgs)} orgs")
        return
    INDEX.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    STATS.write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    ORGS.write_text(json.dumps(orgs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    t = stats["totals_1y"]
    print(f"data/index.json: {len(incidents)} incidents · stats: 1y {t['incidents']} incidents, affected {t['affected_sum']:,} · orgs {len(orgs)} · seen {len(seen)}")


if __name__ == "__main__":
    main()
