# data-leak-search

情報漏洩（データブリーチ）の公開情報を毎朝 Claude Code のルーチンで集め、事案単位に整理して検索・追跡できる静的サイト。
作りは [tech-research](https://github.com/ke-iwata/tech-research) と同じ（ビルド不要の HTML + `data/*.json`、標準ライブラリのみの Python スクリプト、GitHub Pages）。

- 設計書: [docs/design.md](docs/design.md)
- 漏洩したデータそのものや個人名は扱わない。組織の公表・公的機関・メディアの公開情報のメタデータのみ
