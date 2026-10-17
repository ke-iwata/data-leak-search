# data-leak-search

情報漏洩（データブリーチ）の公開情報を毎朝集め、事案（Incident）単位の台帳として `data/incidents/` に蓄積し、
GitHub Pages のサイト（`index.html`）で検索・追跡できるようにするリポジトリ。
サイトはビルド不要の静的 HTML で、`data/*.json` を読んで表示する。設計は [docs/design.md](docs/design.md)。

**このリポジトリは public。** 持ち主の個人情報（氏名・住所・勤務先など）は、ファイルにもコミットメッセージにも絶対に書かないこと。
**事案に個人名を書かない。** 主体は組織のみ。内部不正の従業員名、被害者名、報道に出た個人名は記録しない。
**漏えいしたデータそのもの**（個人情報・認証情報・ダンプ）は収集・保存・表示しない。扱うのは「どこで・いつ・何が・どれだけ・なぜ」のメタデータだけ。
噂や未確認のリーク主張は載せない。組織自身・公的機関・信頼できるメディアのいずれかが公表した事案のみ。

---

## 日次更新ルーチン

Claude Code のルーチンから「CLAUDE.md の日次更新ルーチンを実行して」と呼ばれたら、以下を上から順に実行する。
無人実行なので質問はせず、判断した前提は `daily` の `skipped` や timeline の `text` に残す。今日の日付を D（Asia/Tokyo、`YYYY-MM-DD`）とする。

### 0. 最新の main から始める

**更新は main に直接 push する。** 作業ブランチ（`claude/…`）に push しただけでは Pages の公開に失敗することがあるため、必ず main を更新する。

```bash
git fetch origin main
git checkout -B main origin/main         # セッションが claude/… ブランチで始まっていても main で作業する
```

### 1. 収集（スクリプト）

```bash
python3 scripts/collect.py      # RSS / HTML / HIBP → data/raw/D.json（コミットしない）
```

- `data/raw/D.json` の `candidates` は候補。`leak_score` と `matched_keywords` はキーワード照合の目安で、事案かどうかは自分で判断する
- `maybe_update_of` に既存事案 id があるものは**続報の可能性**。先にその事案（`data/incidents/YYYY-MM.json`）を開いて、組織・時期・原因が一致するか確かめる
- `seen: true` は処理済み URL（既に出典として使った、または過去に `skipped` にした）。同じ URL を二度扱わない
- `source_status` で `failed` のソースは、`data/daily/D.json` の `stats.failed_sources` に列挙する（深追いしない）
- 候補が 0 件でも、手順 5 の daily ファイルは書く（`new: []`、digest は「新規なし」の 1 件）

### 2. 候補の選別

候補を上から見て、次に当てはまるものは `skipped` に理由を書いて捨てる（5 件まで記録、残りは数だけ `stats.skipped_count` に）。

- 事案ではない: 脆弱性情報、注意喚起のみ、統計・調査レポート、製品紹介、まとめ記事で個別事案を特定できないもの
- 組織の公表も公的機関の公表も確認できず、メディア 1 社の報道だけで組織名が出ていないもの
- 1 つの記事に複数の事案が含まれる場合は、事案ごとに分けて扱う（Security NEXT の「〜など」形式に注意）

### 3. 一次出典を読む（WebFetch）

- メディア記事の場合、本文中の**組織の発表 URL**（お知らせ、お詫び、IR）を探して WebFetch で本文を読む。見つかれば `sources` の先頭に `role: "primary"`, `trust: "official"` で入れ、`confidence: 0.9`
- 公的機関（個人情報保護委員会など）の公表は `trust: "authority"`。それだけでも `role: "primary"`, `confidence: 0.9`
- 組織の発表が見つからず、メディア記事の本文を読めた場合は `role: "secondary"`, `trust: "media"`, `confidence: 0.7`
- 見出しと RSS の要約しか読めなかった場合は `confidence: 0.5`。数値や日付は見出しに書かれたものだけ
- HIBP 由来（`hibp` フィールドあり）は `trust: "aggregator"`, `confidence: 0.6`。`disclosure_url` があれば WebFetch して primary にできないか試す。`is_spam_list` / `is_stealer_log` が true のものは事案ではないので skip
- WebFetch は 1 回のルーチンで 20 回程度まで。読めなかった URL は無理に読まず、`confidence` を下げる

### 4. 構造化（新規 or 続報）

**新規**: `data/incidents/YYYY-MM.json`（`disclosed_at` の月）の `incidents` 配列に追加する。ファイルが無ければ `{"month": "YYYY-MM", "incidents": []}` で作る。
**続報**: 該当事案の `sources` に出典を追加し、`timeline` に `kind: "updated"`（報告書なら `report_filed`、終息なら `resolved`）を足し、`affected` / `cause` / `data_types` / `status` / `summary` を更新して `updated_at` を D にする。**変更前の値は timeline の text に残す**（例「影響人数を 52,000 件から 123,000 件に修正」）。

```json
{
  "id": "2026-10-01-sample-corp",            // disclosed_at + "-" + org。全期間で一意
  "org": "sample-corp",                       // 組織 slug。data/orgs.json の既存 slug を最優先で再利用する
  "org_name": "株式会社サンプル",              // 表示名。法人格は公表どおり
  "org_aliases": ["サンプル", "Sample Corp."],  // 別名・略称・サービス名・ドメイン。collect.py の照合に使う
  "industry": "retail",                       // config.industries[].id
  "country": "JP",                            // ISO 3166-1 alpha-2
  "title": "株式会社サンプル 不正アクセスによる顧客情報漏えい",
  "summary": "EC サイトが不正アクセスを受け、顧客情報 12 万件が漏えいした可能性。カード情報を含む。",  // 2〜3 文、自分の言葉で
  "cause": "unauthorized_access",             // config.causes[].id
  "vector": "EC サイトの脆弱性を突かれ決済ページを改ざん",  // 原因の具体。分からなければ null
  "data_types": ["name", "address", "card"],  // config.data_types[].id
  "affected": 123456,                         // 本文に書かれた件数。不明なら null
  "affected_is_estimate": false,              // 「最大」「約」「可能性」なら true
  "occurred_at": "2026-08-10",                // 発生（期間なら開始日）。不明なら null
  "detected_at": "2026-09-20",                // 不明なら null
  "disclosed_at": "2026-10-01",               // 最初の公表日。id とファイルの月はこれで決まる
  "status": "disclosed",                      // disclosed / investigating / reported / closed / unconfirmed
  "confidence": 0.9,
  "sources": [
    {"role": "primary", "trust": "official", "publisher": "株式会社サンプル",
     "url": "https://example.co.jp/news/2026/1001.html", "published_at": "2026-10-01", "title": "…"},
    {"role": "secondary", "trust": "media", "publisher": "Security NEXT",
     "url": "https://www.security-next.com/…", "published_at": "2026-10-02", "title": "…"}
  ],
  "timeline": [
    {"date": "2026-08-10", "kind": "occurred",  "text": "不正アクセスの開始（推定）", "source": 0},
    {"date": "2026-09-20", "kind": "detected",  "text": "決済代行会社からの連絡で判明", "source": 0},
    {"date": "2026-10-01", "kind": "disclosed", "text": "第一報を公表。EC サイトを停止", "source": 0}
  ],
  "keywords": ["ec-site", "payment-skimming"], // 小文字・数字・ハイフンの slug。既存 slug を優先
  "created_at": "2026-10-02",
  "updated_at": "2026-10-02"
}
```

- `timeline.kind` は `occurred` / `detected` / `disclosed` / `updated` / `report_filed` / `resolved`。`source` は `sources` のインデックス（無ければ null）
- `affected` は本文に書かれた数字だけ。「最大」「約」「可能性」は `affected_is_estimate: true`。推測で埋めない
- 日付は本文で確認できたものだけ。「9 月中旬」のように日が特定できなければ null にして timeline の text に書く
- `org` slug は `data/orgs.json` を見て既存を使う。新規なら組織名のローマ字か英語名を小文字・ハイフンで（例 `sample-corp`, `kitasakae-city`, `midori-hospital`）。自治体は `-city` / `-pref`、病院は `-hospital`、学校は `-school` / `-univ` を付ける
- `org_aliases` には照合に効く表記（略称、サービス名、ドメイン）を入れる。ただし短すぎる語（1 文字、一般名詞）は入れない
- `status`: 第一報のみ `disclosed`、調査継続中と明記 `investigating`、報告書公表・当局報告済 `reported`、終息 `closed`。リークサイト観測のみで組織の公表が無いもの `unconfirmed`（MVP では原則作らない）
- **個人名・個人のメールアドレス・カード番号などは一切書かない**（`update_stats.py` が検出すると検証エラーになる）
- 記事本文は転載しない。`summary` と `timeline.text` は事実の要約を自分の言葉で

### 5. `data/daily/D.json` を書く

```json
{
  "date": "2026-10-17",
  "generated_at": "2026-10-17T06:10:00+09:00",
  "stats": {"collected": 84, "candidates": 19, "sources_ok": 7, "sources_total": 7, "failed_sources": [], "skipped_count": 8},
  "digest": [                                   // 0〜5 件。その日の傾向や大規模事案を 1〜2 文で
    {"text": "小売の EC サイト改ざんによるカード情報漏えいが今週 3 件。", "incidents": ["2026-10-01-sample-corp"]}
  ],
  "new": ["2026-10-16-foo-city"],                // 今日作った事案 id
  "updated": [{"id": "2026-10-01-sample-corp", "text": "件数を 12 万件に修正"}],
  "skipped": [{"url": "https://…", "reason": "脆弱性情報で事案ではない"}]   // 5 件まで
}
```

### 6. 検証・集計・コミット・push

```bash
python3 scripts/update_stats.py        # 検証して index.json / stats.json / orgs.json / state/seen.json を再生成。エラーなら直してやり直す
git add -A
git commit -m "data: D 日次更新（新規N件・続報N件）"
git push origin HEAD:main                # main を更新する（これで Actions が Pages に公開する）
```

- push が拒否されたら `git pull --rebase origin main` してから `python3 scripts/update_stats.py` をやり直し、もう一度 `git push origin HEAD:main` する
- それでも main に push できないとき（権限エラーなど）だけ、`claude/` で始まるブランチに push する。Actions が main にマージして公開するが、main の更新に失敗した旨とエラー全文を最終メッセージに書く
- `data/raw/` は `.gitignore` 済み（コミットしない）。`data/index.json` などの生成物はコミットする
- 最後に digest と新規・続報の一覧を最終メッセージとして出力する

---

## ファイル構成

| パス | 内容 | 書き手 |
|---|---|---|
| `index.html`, `assets/app.js`, `assets/style.css` | サイト本体（ダーク基調の監視ボード様式。TODAY / INCIDENTS / ORGS / STATS）。`data/*.json` を読んで描画する | 人 |
| `CLAUDE.md` | このファイル。ルーチンの手順とスキーマ | 人 |
| `data/config.json` | 情報源、原因・業種・データ種別のマスタ、漏えいキーワード、除外キーワード | 人 |
| `data/raw/YYYY-MM-DD.json` | 収集した候補（下書き）。コミットしない | `collect.py` |
| `data/incidents/YYYY-MM.json` | 事案の台帳。`disclosed_at` の月で分割 | ルーチン |
| `data/daily/YYYY-MM-DD.json` | その日の要点・新規/続報の事案 id・収集統計 | ルーチン |
| `data/index.json` | 全事案の軽量インデックス（一覧・検索用） | `update_stats.py` |
| `data/stats.json` | 月次・原因別・業種別・データ種別・規模分布・上位・日次件数 | `update_stats.py` |
| `data/orgs.json` | 組織 slug → 表示名・別名・事案 id | `update_stats.py` |
| `data/state/seen.json` | 処理済み URL（事案の出典 + skipped） | `update_stats.py` |
| `scripts/collect.py` | RSS / HTML / HIBP の収集と照合ヒント | 人 |
| `scripts/update_stats.py` | 検証と生成 | 人 |
| `.github/workflows/pages.yml` | claude/** → main マージと Pages デプロイ | 人 |

## 収集元を増やすとき

- RSS は `data/config.json` の `sources` に `{id, name, kind: "rss", trust, url}` を追加する。`python3 scripts/collect.py --dry-run` で取得できるか確認してからコミットする
- 一覧ページ（HTML）は `kind: "html", parser: "time_link"`。`<time datetime="YYYY-MM-DD">` の直後の `<a>` を 1 件として拾う。別の構造のページは `collect.py` にパーサを足す
- 原因・業種・データ種別を増やすときは `config.json` に id を足し、サイト側のラベル表示も追従させる。既存 id は変えない（過去の事案が検証エラーになる）

ローカル確認：`python3 -m http.server` を実行して http://localhost:8000 を開く。
