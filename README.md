# data-leak-search

情報漏洩（データブリーチ）の公開情報を毎朝 Claude Code のルーチンで集め、事案単位の台帳として蓄積し、GitHub Pages で検索・追跡できるようにする自分用サイトです。
作りは [tech-research](https://github.com/ke-iwata/tech-research) と同じ（ビルド不要の HTML + `data/*.json`、標準ライブラリのみの Python スクリプト、GitHub Pages）。

- 設計書: [docs/design.md](docs/design.md)
- 更新手順: [CLAUDE.md](CLAUDE.md)
- 扱うのは組織の公表・公的機関・メディアが公開した事案のメタデータのみ。漏えいしたデータそのものや個人名は扱いません
- 要約は自動生成で誤りを含むことがあります。必ず出典の一次情報で確認してください

ローカル確認：`python3 -m http.server` → http://localhost:8000
