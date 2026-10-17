# Workflow Sync (ComfyUI x LLM)

**One workflow, edited together by you and an LLM agent.**

Workflow Sync is a ComfyUI extension that keeps a single workflow in sync
between the ComfyUI front-end and an LLM agent that drives ComfyUI through the
official [Comfy MCP](https://github.com/Comfy-Org/comfy-mcp). The agent reads and
writes the shared workflow, the extension mirrors it into the ComfyUI canvas in
real time, and both sides can press **Run**.

- Version: **1.0.0**
- License: **Apache-2.0**
- No third-party service. Uses only the stock ComfyUI server and the official Comfy MCP.

---

## Features

- **Shared workflow files** - a UI graph and its generated API graph are kept on
  disk, so the LLM can read, edit and validate the same graph the canvas shows.
- **Live screen sync** - edits are reflected in the open ComfyUI screen
  (~750 ms polling plus push notifications).
- **Auto-open** - when the LLM edits the workflow, the extension can open it in
  the ComfyUI screen automatically.
- **Conflict handling** - when the canvas and the LLM both change, the chosen
  resolution is applied, and the pre-sync state is kept so you can restore it.
- **Seed controls** - random / fixed seed policy is shared with the LLM run.
- **Localized UI** - English, Japanese, Chinese and Korean (follows `Comfy.Locale`).

---

## Requirements

- ComfyUI (desktop or source) with the front-end extension support.
- The official **Comfy MCP** (`comfy-mcp` + `comfy-cli`) on the agent side, if you
  want an LLM agent to drive ComfyUI.
- No extra Python packages are required by this extension.

---

## Installation

### ComfyUI Manager (recommended)

1. Open **ComfyUI Manager**.
2. Search for **Workflow Sync**.
3. Install, then **restart ComfyUI** and reload the browser tab.

### Manual (git clone)

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/your-account/comfyui_workflow_sync
```

### Manual (copy)

Copy the `comfyui_workflow_sync` folder into `ComfyUI/custom_nodes/`.

After installing, **restart ComfyUI** and **reload the page**. A folder named
`comfyui_workflow_sync` must appear under `custom_nodes`.

---

## How it works

The extension owns three files inside `ComfyUI/user/default/workflows/`:

| File | Purpose |
| --- | --- |
| `Workflow collaboration.ui.json` | UI (serialized) graph - edited by people and the LLM |
| `Workflow collaboration.api.json` | API (prompt) graph - what actually runs |
| `Workflow collaboration.sync.json` | revision and seed-policy metadata |

Small HTTP endpoints on the ComfyUI server do the synchronization:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/workflow-sync/state` | current revision, graphs, jobs, editor lease |
| `POST` | `/workflow-sync/lease` | claim / renew the editor lease |
| `POST` | `/workflow-sync/state` | publish this screen's graph |

The front-end sends the API graph that ComfyUI derives with `graphToPrompt`, so
the LLM always runs the exact graph you can see.

---

## Setup with the official Comfy MCP

Register the MCP server with your agent client. Example (`mcpServers` config):

```json
{
  "mcpServers": {
    "comfyui": {
      "command": "C:\\path\\to\\comfy-mcp.exe",
      "env": {
        "COMFY_BIN": "C:\\path\\to\\comfy.exe",
        "COMFY_PROJECT": "C:\\path\\to\\ComfyUI",
        "COMFY_LOCAL_URL": "http://127.0.0.1:8188",
        "COMFY_WHERE": "local"
      }
    }
  }
}
```

Then tell the agent to use the shared files, for example:

1. Read `Workflow collaboration.ui.json` (and the graph on screen).
2. Change values with the official `set_workflow_slot` (`stdout=False`).
3. Confirm `Workflow collaboration.api.json` is non-empty and contains the change.
4. Run `validate_workflow`, then `run_workflow(wait=False)`, `job(action="status")`
   and `fetch_outputs(url_only=True, inline_images=False)`.

For image edits, upload the source image with `upload_file`, connect it to
`LoadImage`, and re-run. Do not replace an image edit with a fresh text-to-image run.

---

## Settings

ComfyUI **Settings -> Workflow Sync**:

| Setting | Default | Description |
| --- | --- | --- |
| Sync the workflow with the LLM | On | Master switch for the extension |
| Auto-open the workflow the LLM edited | On | Load the LLM's graph into this screen |
| On sync conflict | **Load the LLM changes** | What to do when both sides changed |

`On sync conflict` options:

- **Ask me** - do nothing until you choose.
- **Keep my edits** - republish this screen (your edits win).
- **Load the LLM changes** *(default)* - take the LLM's graph; your pre-sync
  graph is saved and can be restored.
- **Restore before sync** - bring back the graph from before the first sync.

---

## Localization

The UI follows the ComfyUI language (`Comfy.Locale`) and falls back to the
browser language. Bundled languages: **English, Japanese, Chinese, Korean**.

Strings live in `workflow-sync.js` and are mirrored in `locales/*.json` for
translators. To add a language, copy `locales/en.json`, translate the values, and
add the code to `LANGS` and `I18N` in `workflow-sync.js`.

---

## Repository layout

```
comfyui_workflow_sync/
├── __init__.py          # backend: endpoints, file sync, job mirroring
├── workflow-sync.js     # frontend: settings, screen sync, localization
├── pyproject.toml       # Comfy Registry / Manager metadata (v1.0.0)
├── LICENSE              # Apache-2.0
├── NOTICE
├── README.md
├── locales/             # en / ja / zh / ko
└── tests/
    └── test_auto_open.py
```

---

## Compatibility and limitations

- Requires a recent ComfyUI front-end (tested with ComfyUI 0.41.x / frontend 1.55.x).
- One screen holds the editor lease at a time; a second screen can take over
  after the first stops syncing (about 8 seconds).
- The extension does not add generation nodes, and does not modify the official
  Comfy MCP or ComfyUI source.

---

## License

Apache License 2.0 - see [LICENSE](LICENSE).

---

## Changelog

### 1.0.0

- First public release.
- Multilingual UI (English, Japanese, Chinese, Korean).
- Default conflict resolution is now **Load the LLM changes**.
- Comfy Registry / ComfyUI Manager packaging (`pyproject.toml`).

---

# Workflow Sync（ComfyUI × LLM）

**1つのワークフローを、あなたと LLM エージェントが一緒に編集する。**

Workflow Sync は、ComfyUI の画面と、公式 [Comfy MCP](https://github.com/Comfy-Org/comfy-mcp)
経由で ComfyUI を操作する LLM エージェントの間で、**同じワークフローを同期**する
ComfyUI 拡張です。LLM が共有ファイルを読み書きし、拡張がそれを画面へ即時反映し、
どちらからでも **Run** できます。

- バージョン: **1.0.0**
- ライセンス: **Apache-2.0**
- 第三者のサービスは使いません。ComfyUI 標準サーバーと公式 Comfy MCP のみです。

## 主な機能

- **共有ワークフローファイル** - UI グラフと、そこから生成される API グラフを
  ファイルに保存し、LLM が画面と同じグラフを読み・編集・検証できます。
- **画面のライブ同期** - 変更は開いている ComfyUI 画面へ反映されます（約750ms 間隔＋通知）。
- **自動オープン** - LLM がワークフローを編集したら、画面へ自動で開けます。
- **競合処理** - 画面と LLM が両方変更した場合、選んだ方針を適用し、
  同期前の状態を保存して復元できます。
- **シード制御** - 固定 / ランダムの方針を LLM 実行と共有します。
- **多言語 UI** - 英語・日本語・中国語・韓国語（`Comfy.Locale` に追従）。

## 必要環境

- ComfyUI（デスクトップ版 / ソース版）と、拡張機能対応のフロントエンド。
- LLM エージェントから操作する場合は、公式 **Comfy MCP**（`comfy-mcp` + `comfy-cli`）。
- この拡張自体に追加の Python パッケージは不要です。

## インストール

### ComfyUI Manager（推奨）

1. **ComfyUI Manager** を開く。
2. **Workflow Sync** を検索。
3. インストール後、**ComfyUI を再起動**し、ページを再読み込み。

### 手動（git clone）

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/your-account/comfyui_workflow_sync
```

### 手動（コピー）

`comfyui_workflow_sync` フォルダを `ComfyUI/custom_nodes/` にコピーします。

インストール後は **ComfyUI を再起動**して**ページを再読み込み**してください。
`custom_nodes` の下に `comfyui_workflow_sync` フォルダがあれば導入成功です。

## 仕組み

拡張は `ComfyUI/user/default/workflows/` に3つのファイルを作ります。

| ファイル | 役割 |
| --- | --- |
| `Workflow collaboration.ui.json` | UI（シリアライズ）グラフ。人と LLM が編集 |
| `Workflow collaboration.api.json` | API（実行用）グラフ。実際に動くのはこちら |
| `Workflow collaboration.sync.json` | revision とシード方針のメタ情報 |

ComfyUI サーバーに同期用の小さな HTTP エンドポイントを追加します。

| メソッド | パス | 用途 |
| --- | --- | --- |
| `GET` | `/workflow-sync/state` | 現在の revision・グラフ・ジョブ・編集権 |
| `POST` | `/workflow-sync/lease` | 編集権の取得・更新 |
| `POST` | `/workflow-sync/state` | 画面のグラフを公開 |

画面側は ComfyUI が `graphToPrompt` で生成した API グラフを送るため、LLM は
**画面に見えているのと同じグラフ**を実行します。

## 公式 Comfy MCP との接続

エージェント側に MCP サーバーを登録します（例）。

```json
{
  "mcpServers": {
    "comfyui": {
      "command": "C:\\path\\to\\comfy-mcp.exe",
      "env": {
        "COMFY_BIN": "C:\\path\\to\\comfy.exe",
        "COMFY_PROJECT": "C:\\path\\to\\ComfyUI",
        "COMFY_LOCAL_URL": "http://127.0.0.1:8188",
        "COMFY_WHERE": "local"
      }
    }
  }
}
```

エージェントへの指示例：

1. `Workflow collaboration.ui.json`（と画面のグラフ）を読む。
2. 値の変更は公式 `set_workflow_slot`（`stdout=False`）。
3. `Workflow collaboration.api.json` が非空で変更を含むことを確認。
4. `validate_workflow` → `run_workflow(wait=False)` → `job(action="status")`
   → `fetch_outputs(url_only=True, inline_images=False)` の順で実行。

画像編集では、元画像を `upload_file` で送り `LoadImage` に接続して再実行します。
画像編集を「プロンプトだけの新規生成」で代用しないでください。

## 設定

ComfyUI の **設定 → Workflow Sync**：

| 設定 | 既定値 | 説明 |
| --- | --- | --- |
| LLMとのワークフロー同期 | ON | 拡張の有効 / 無効 |
| LLMが操作したワークフローを自動で開く | ON | LLM のグラフを画面へ読み込む |
| 同期が競合したときの操作 | **LLMの変更を読み込む** | 双方が変更したときの動作 |

「同期が競合したときの操作」の選択肢：

- **選択してください** - 選ぶまで何もしない。
- **画面の編集を採用** - この画面を再公開（画面を優先）。
- **LLMの変更を読み込む**（既定）- LLM のグラフを採用。同期前のグラフは保存され復元可能。
- **同期前の画面を復元** - 最初の同期前のグラフに戻す。

## 多言語

UI は ComfyUI の言語設定（`Comfy.Locale`）に追従し、無ければブラウザ言語を使います。
同梱言語は **英語・日本語・中国語・韓国語**。文言は `workflow-sync.js` にあり、
翻訳者向けに `locales/*.json` へも同内容を置いています。言語を追加するには
`locales/en.json` を複製して翻訳し、`workflow-sync.js` の `LANGS` と `I18N` に
コードを追加します。

## ライセンス

Apache License 2.0 - [LICENSE](LICENSE) を参照。

## 変更履歴

### 1.0.0

- 初回公開リリース。
- 多言語 UI（英語・日本語・中国語・韓国語）。
- 競合時の既定動作を **「LLMの変更を読み込む」** に変更。
- Comfy Registry / ComfyUI Manager 形式（`pyproject.toml`）に対応。

---

# Workflow Sync（中文简述）

Workflow Sync 是一个 ComfyUI 扩展，让 **同一个工作流** 在你和通过官方
[Comfy MCP](https://github.com/Comfy-Org/comfy-mcp) 操作 ComfyUI 的 LLM 代理之间保持同步：
共享 UI/API 文件、画面实时同步、自动打开、冲突处理与随机种子策略共享。

- 版本：**1.0.0**，许可证：**Apache-2.0**，不使用任何第三方服务。
- 安装：在 **ComfyUI Manager** 中搜索 **Workflow Sync**，或把
  `comfyui_workflow_sync` 复制到 `ComfyUI/custom_nodes/`，然后重启 ComfyUI。
- 设置：**设置 → Workflow Sync**。「同步冲突时的操作」默认是
  **「载入 LLM 的更改」**（可选：请选择 / 采用画面编辑 / 同步前恢复）。
- 语言：随 `Comfy.Locale` 切换，内置英语、日语、中文、韩语。
- 与 MCP 配合：读取 `Workflow collaboration.ui.json`，用官方
  `set_workflow_slot(stdout=False)` 修改，确认 `Workflow collaboration.api.json`
  已更新后执行 `validate_workflow` → `run_workflow` → `job` → `fetch_outputs`。
