# HMN Web Docs Module Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 在 `agent.misk.cc` / HMN 控制面内提供一个 `hmn-web` 文档实时查阅模块，直接浏览 `/srv/files/docs/server` 和 `/srv/files/service`，不再通过 `file.misk.cc` 或修改 Caddy 静态挂载。

**Architecture:** 后端先用 FastAPI 原生 HTML + JSON + 文件读取实现轻量 `hmn-web` 模块，作为控制面的一部分挂在 `/hmn-web/docs`。文档从 `/srv/files` 实时读取，只允许 `.md` / `.json` / `.txt` 等安全文本文件，路径必须限制在 docs root 内。后续如需要前端 SPA，可在同一路由上渐进替换。

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, HTMLResponse, FileResponse/Response, `/srv/files` docs center.

---

## Non-goals / Safety Rails

- 不修改 `/etc/caddy/Caddyfile`。
- 不修改 `file.misk.cc` / `status.misk.cc` / Cloudflare DNS。
- 不引入 Node/Vite/React，除非后续明确要做 SPA。
- 不提供在线编辑；本期只读。
- 不显示或写入敏感 token。
- 不允许路径穿越读取 `/srv/files` 外文件。

## Target URLs

```text
https://agent.misk.cc/hmn-web/docs
https://agent.misk.cc/hmn-web/docs/file/docs/server/README.md
https://agent.misk.cc/hmn-web/docs/file/service/README.md
https://agent.misk.cc/api/v1/hmn-web/docs/index
```

---

### Task 1: Clean up partial docs-module scaffolding into an explicit test baseline

**Objective:** 确认当前半成品改动不会混乱 1.1 后台任务；给 hmn-web docs 模块建立清晰测试入口。

**Files:**
- Modify: `tests/test_api.py`
- Inspect: `src/hermes_managed_network/api.py`

**Step 1: Write/normalize failing tests**

在 `tests/test_api.py` 顶部保留或整理这两个测试：

```python
def test_hmn_web_docs_module_serves_docs_index_and_markdown(tmp_path):
    docs_root = tmp_path / "files"
    server_dir = docs_root / "docs" / "server"
    service_dir = docs_root / "service"
    server_dir.mkdir(parents=True)
    service_dir.mkdir(parents=True)
    (server_dir / "README.md").write_text("# Servers\n\n- demo\n", encoding="utf-8")
    (service_dir / "demo.md").write_text("# Demo Service\n", encoding="utf-8")

    client = TestClient(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    index_response = client.get("/hmn-web/docs")
    assert index_response.status_code == 200
    assert "HMN 文档中心" in index_response.text
    assert "/hmn-web/docs/file/docs/server/README.md" in index_response.text
    assert "/hmn-web/docs/file/service/demo.md" in index_response.text

    markdown_response = client.get("/hmn-web/docs/file/docs/server/README.md")
    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")
    assert markdown_response.text == "# Servers\n\n- demo\n"


def test_hmn_web_docs_module_rejects_path_traversal(tmp_path):
    docs_root = tmp_path / "files"
    docs_root.mkdir()
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/hmn-web/docs/file/../secret.md")

    assert response.status_code in {400, 404}
```

**Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_api.py::test_hmn_web_docs_module_serves_docs_index_and_markdown \
       tests/test_api.py::test_hmn_web_docs_module_rejects_path_traversal -q
```

Expected: FAIL — `create_app() got an unexpected keyword argument 'docs_root'` or route 404.

**Step 3: Do not implement yet**

只确认 RED。不要在本任务写生产代码。

**Step 4: Commit**

如果单独提交测试：

```bash
git add tests/test_api.py
git commit -m "test(web): cover hmn docs module"
```

---

### Task 2: Add safe docs root injection to FastAPI app

**Objective:** 让测试可传入临时 docs root，生产默认 `/srv/files`。

**Files:**
- Modify: `src/hermes_managed_network/api.py:22-24,292`
- Test: `tests/test_api.py`

**Step 1: Implement minimal signature**

```python
DEFAULT_DOCS_ROOT = Path("/srv/files")


def create_app(
    db_path: str | Path = DEFAULT_DB,
    *,
    docs_root: str | Path = DEFAULT_DOCS_ROOT,
) -> FastAPI:
    app = FastAPI(title="Hermes Managed Network", version="0.2.0")
    docs_root = Path(docs_root).expanduser()
```

注意：`docs_root` 必须是 keyword-only，避免破坏现有 `create_app(db)` 调用。

**Step 2: Run focused test**

```bash
pytest tests/test_api.py::test_hmn_web_docs_module_serves_docs_index_and_markdown -q
```

Expected: 仍 FAIL — route 404。

**Step 3: Commit**

```bash
git add src/hermes_managed_network/api.py
git commit -m "feat(web): allow injecting docs root"
```

如果 shell 里误复制了 `巨`，修正为：

```bash
git commit -m "feat(web): allow injecting docs root"
```

---

### Task 3: Add safe path resolver for docs files

**Objective:** 防止 `/hmn-web/docs/file/../secret` 读取 docs root 外部文件。

**Files:**
- Modify: `src/hermes_managed_network/api.py`
- Test: `tests/test_api.py`

**Step 1: Add helper near `_notification_response`**

```python
def _resolve_docs_file(docs_root: Path, relative_path: str) -> Path:
    root = docs_root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        raise HTTPException(status_code=400, detail="invalid docs path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="docs file not found")
    if candidate.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml"}:
        raise HTTPException(status_code=404, detail="unsupported docs file")
    return candidate
```

**Step 2: Run traversal test**

```bash
pytest tests/test_api.py::test_hmn_web_docs_module_rejects_path_traversal -q
```

Expected: 仍 FAIL until route exists；helper itself should lint.

**Step 3: Commit**

```bash
git add src/hermes_managed_network/api.py
git commit -m "feat(web): add safe docs path resolver"
```

---

### Task 4: Serve raw markdown docs under hmn-web

**Objective:** 实现 `/hmn-web/docs/file/{path}` 实时读取 Markdown。

**Files:**
- Modify: `src/hermes_managed_network/api.py`
- Test: `tests/test_api.py`

**Step 1: Add route inside `create_app` after `/api/v1/console/services`**

```python
    @app.get("/hmn-web/docs/file/{relative_path:path}", include_in_schema=False)
    def hmn_web_docs_file(relative_path: str) -> Response:
        path = _resolve_docs_file(docs_root, relative_path)
        media_type = "text/markdown; charset=utf-8" if path.suffix.lower() == ".md" else "text/plain; charset=utf-8"
        if path.suffix.lower() == ".json":
            media_type = "application/json; charset=utf-8"
        return Response(path.read_text(encoding="utf-8"), media_type=media_type)
```

**Step 2: Run focused tests**

```bash
pytest tests/test_api.py::test_hmn_web_docs_module_serves_docs_index_and_markdown \
       tests/test_api.py::test_hmn_web_docs_module_rejects_path_traversal -q
```

Expected: first still FAIL on index route; markdown part would pass once index exists. second PASS or 404/400.

**Step 3: Commit**

```bash
git add src/hermes_managed_network/api.py tests/test_api.py
git commit -m "feat(web): serve docs files from hmn web"
```

---

### Task 5: Add docs index data builder

**Objective:** 生成服务文档和机器文档列表，供 HTML 和 JSON 共用。

**Files:**
- Modify: `src/hermes_managed_network/api.py`
- Test: add one JSON endpoint test in `tests/test_api.py`

**Step 1: Write failing JSON test**

```python
def test_hmn_web_docs_index_api_lists_server_and_service_docs(tmp_path):
    docs_root = tmp_path / "files"
    (docs_root / "docs" / "server" / "demo").mkdir(parents=True)
    (docs_root / "docs" / "server" / "demo" / "README.md").write_text("# Demo Node", encoding="utf-8")
    (docs_root / "service").mkdir(parents=True)
    (docs_root / "service" / "api.md").write_text("# API", encoding="utf-8")

    client = TestClient(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/api/v1/hmn-web/docs/index")

    assert response.status_code == 200
    assert response.json() == {
        "server_docs": [
            {"title": "demo/README.md", "path": "docs/server/demo/README.md", "url": "/hmn-web/docs/file/docs/server/demo/README.md"}
        ],
        "service_docs": [
            {"title": "api.md", "path": "service/api.md", "url": "/hmn-web/docs/file/service/api.md"}
        ],
    }
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_api.py::test_hmn_web_docs_index_api_lists_server_and_service_docs -q
```

Expected: FAIL — 404.

**Step 3: Add helper**

```python
def _list_docs(root: Path, subdir: str) -> list[dict[str, str]]:
    base = root / subdir
    if not base.exists():
        return []
    items: list[dict[str, str]] = []
    for path in sorted(base.rglob("*.md")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        title = path.relative_to(base).as_posix()
        items.append({
            "title": title,
            "path": relative,
            "url": f"/hmn-web/docs/file/{relative}",
        })
    return items


def _docs_index_payload(root: Path) -> dict[str, list[dict[str, str]]]:
    return {
        "server_docs": _list_docs(root, "docs/server"),
        "service_docs": _list_docs(root, "service"),
    }
```

**Step 4: Add route**

```python
    @app.get("/api/v1/hmn-web/docs/index")
    def hmn_web_docs_index_api() -> dict[str, list[dict[str, str]]]:
        return _docs_index_payload(docs_root)
```

**Step 5: Run test**

```bash
pytest tests/test_api.py::test_hmn_web_docs_index_api_lists_server_and_service_docs -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/hermes_managed_network/api.py tests/test_api.py
git commit -m "feat(web): expose docs index api"
```

---

### Task 6: Add mobile-friendly HTML docs index

**Objective:** `/hmn-web/docs` 显示可点击文档列表，适合手机查看。

**Files:**
- Modify: `src/hermes_managed_network/api.py`
- Test: `tests/test_api.py`

**Step 1: Implement HTML renderer**

```python
def _render_docs_index_html(payload: dict[str, list[dict[str, str]]]) -> str:
    def section(title: str, items: list[dict[str, str]]) -> str:
        links = "\n".join(
            f'<li><a href="{escape(item["url"])}">{escape(item["title"])}</a></li>'
            for item in items
        ) or "<li>暂无文档</li>"
        return f"<section><h2>{escape(title)}</h2><ul>{links}</ul></section>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HMN 文档中心</title>
  <style>
    body {{ margin: 0; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #1d1d1f; }}
    main {{ max-width: 880px; margin: 0 auto; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    p {{ color: #6e6e73; }}
    section {{ background: white; border-radius: 18px; padding: 18px; margin: 16px 0; box-shadow: 0 8px 30px rgba(0,0,0,.06); }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{ border-top: 1px solid #eee; }}
    li:first-child {{ border-top: 0; }}
    a {{ display: block; padding: 12px 0; color: #06c; text-decoration: none; word-break: break-all; }}
  </style>
</head>
<body>
  <main>
    <h1>HMN 文档中心</h1>
    <p>实时读取 /srv/files 文档；只读浏览，不经过 file.misk.cc。</p>
    {section("机器文档", payload["server_docs"])}
    {section("服务文档", payload["service_docs"])}
  </main>
</body>
</html>"""
```

**Step 2: Add route**

```python
    @app.get("/hmn-web/docs", response_class=HTMLResponse, include_in_schema=False)
    def hmn_web_docs_index() -> HTMLResponse:
        return HTMLResponse(_render_docs_index_html(_docs_index_payload(docs_root)))
```

**Step 3: Run focused tests**

```bash
pytest tests/test_api.py::test_hmn_web_docs_module_serves_docs_index_and_markdown \
       tests/test_api.py::test_hmn_web_docs_index_api_lists_server_and_service_docs -q
```

Expected: PASS.

**Step 4: Commit**

```bash
git add src/hermes_managed_network/api.py tests/test_api.py
git commit -m "feat(web): add docs index page"
```

---

### Task 7: Add Markdown viewer page wrapper

**Objective:** 原始 Markdown 可直接打开，但手机阅读最好有 HTML 包装页。

**Files:**
- Modify: `src/hermes_managed_network/api.py`
- Test: `tests/test_api.py`

**Step 1: Write failing test**

```python
def test_hmn_web_docs_view_page_wraps_markdown(tmp_path):
    docs_root = tmp_path / "files"
    (docs_root / "service").mkdir(parents=True)
    (docs_root / "service" / "demo.md").write_text("# Demo\n\nhello", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/hmn-web/docs/view/service/demo.md")

    assert response.status_code == 200
    assert "HMN 文档" in response.text
    assert "# Demo" in response.text
    assert "hello" in response.text
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_api.py::test_hmn_web_docs_view_page_wraps_markdown -q
```

Expected: FAIL — 404.

**Step 3: Add simple preformatted viewer**

```python
def _render_markdown_viewer(relative_path: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HMN 文档 - {escape(relative_path)}</title>
  <style>
    body {{ margin: 0; padding: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #1d1d1f; }}
    main {{ max-width: 960px; margin: 0 auto; background: white; border-radius: 18px; padding: 18px; box-shadow: 0 8px 30px rgba(0,0,0,.06); }}
    a {{ color: #06c; }}
    pre {{ white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.55; }}
  </style>
</head>
<body>
  <main>
    <p><a href="/hmn-web/docs">← 返回 HMN 文档中心</a></p>
    <h1>HMN 文档</h1>
    <p>{escape(relative_path)}</p>
    <pre>{escape(content)}</pre>
  </main>
</body>
</html>"""
```

**Step 4: Add route**

```python
    @app.get("/hmn-web/docs/view/{relative_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def hmn_web_docs_view(relative_path: str) -> HTMLResponse:
        path = _resolve_docs_file(docs_root, relative_path)
        return HTMLResponse(_render_markdown_viewer(relative_path, path.read_text(encoding="utf-8")))
```

**Step 5: Run tests**

```bash
pytest tests/test_api.py::test_hmn_web_docs_view_page_wraps_markdown -q
```

Expected: PASS.

**Step 6: Optional index link adjustment**

把 `_list_docs()` 中 `url` 改成 `/hmn-web/docs/view/{relative}`，同时提供 `raw_url` 字段：

```python
"url": f"/hmn-web/docs/view/{relative}",
"raw_url": f"/hmn-web/docs/file/{relative}",
```

如修改 JSON contract，先更新测试再实现。

**Step 7: Commit**

```bash
git add src/hermes_managed_network/api.py tests/test_api.py
git commit -m "feat(web): add markdown docs viewer"
```

---

### Task 8: Add smoke checks for production paths

**Objective:** 部署前确认本地控制面能实时读取 `/srv/files`。

**Files:**
- No source change unless tests reveal issue.

**Step 1: Run focused API tests**

```bash
pytest tests/test_api.py::test_hmn_web_docs_module_serves_docs_index_and_markdown \
       tests/test_api.py::test_hmn_web_docs_module_rejects_path_traversal \
       tests/test_api.py::test_hmn_web_docs_index_api_lists_server_and_service_docs \
       tests/test_api.py::test_hmn_web_docs_view_page_wraps_markdown -q
```

Expected: `4 passed`.

**Step 2: Run all API tests**

```bash
pytest tests/test_api.py -q
```

Expected: all pass.

**Step 3: Run full suite**

```bash
pytest -q
```

Expected: all pass. If unrelated existing failures occur, capture exact failing tests and do not hide them.

**Step 4: Commit if any test-only adjustment**

```bash
git status --short
git add src/hermes_managed_network/api.py tests/test_api.py
git commit -m "test(web): verify docs module integration"
```

---

### Task 9: Deploy/restart control plane only after tests pass

**Objective:** 让 `agent.misk.cc/hmn-web/docs` 生效。

**Files:**
- Runtime service: `hermes-managed-network.service`

**Step 1: Inspect service install path**

```bash
systemctl cat hermes-managed-network.service --no-pager
```

Expected: shows venv/package path, currently seen as `/opt/hermes-managed-network/.venv/bin/python -m hermes_managed_network.server`.

**Step 2: Install updated package if repo is source of service**

Use the project’s existing deployment convention. If editable install is used:

```bash
/opt/hermes-managed-network/.venv/bin/python -m pip install -e /root/hermes-managed-network
```

If not editable, use the existing install script/package flow. Do not guess if service points elsewhere; inspect first.

**Step 3: Restart only HMN service**

```bash
systemctl restart hermes-managed-network.service
systemctl status hermes-managed-network.service --no-pager -l
```

Expected: active/running.

**Step 4: Verify public route through agent.misk.cc**

```bash
curl -fsS https://agent.misk.cc/healthz
curl -fsS https://agent.misk.cc/hmn-web/docs | grep 'HMN 文档中心'
curl -fsS https://agent.misk.cc/hmn-web/docs/file/service/README.md | head
```

Expected:
- healthz JSON OK
- docs page contains `HMN 文档中心`
- service README content prints

**Step 5: Commit deployment notes if docs updated**

Only commit source/docs changes, not runtime state.

---

### Task 10: Add short documentation entry

**Objective:** 记录 hmn-web 文档模块入口，避免以后再误走 `file.misk.cc`。

**Files:**
- Modify: `docs/priority-plan.md` or `README.md` if appropriate
- Better: create/modify service doc through docs center later, but source repo should mention control-plane route.

**Step 1: Add short docs section**

In `README.md` or a relevant docs file:

```markdown
## HMN Web 文档中心

控制面提供只读文档浏览模块：

- 页面：`/hmn-web/docs`
- JSON 索引：`/api/v1/hmn-web/docs/index`
- 原始文件：`/hmn-web/docs/file/<relative-path>`

默认读取 `/srv/files`，包括：

- `/srv/files/docs/server`
- `/srv/files/service`

该模块不依赖 `file.misk.cc`，也不需要修改 Caddy 静态挂载。
```

**Step 2: Run docs-safe tests**

```bash
pytest tests/test_api.py -q
```

Expected: pass.

**Step 3: Commit**

```bash
git add README.md docs/priority-plan.md
git commit -m "docs(web): document hmn docs module"
```

---

## Final Verification Gate

Run:

```bash
git status --short
pytest tests/test_api.py -q
pytest -q
curl -fsS https://agent.misk.cc/healthz
curl -fsS https://agent.misk.cc/hmn-web/docs | grep 'HMN 文档中心'
```

Expected:
- no uncommitted source changes except intentional docs if not committed
- focused and full tests pass
- public route works through `agent.misk.cc`

## Rollback

No Caddy/DNS changes are part of this plan.

If runtime deploy breaks HMN service:

```bash
systemctl status hermes-managed-network.service --no-pager -l
journalctl -u hermes-managed-network.service -n 100 --no-pager
# revert source commit if needed
git revert <hmn-web-docs-commit>
/opt/hermes-managed-network/.venv/bin/python -m pip install -e /root/hermes-managed-network
systemctl restart hermes-managed-network.service
```

## Acceptance Criteria

- `https://agent.misk.cc/hmn-web/docs` shows a mobile-friendly docs index.
- `https://agent.misk.cc/hmn-web/docs/file/service/README.md` returns current Markdown from `/srv/files/service/README.md`.
- `https://agent.misk.cc/api/v1/hmn-web/docs/index` returns server/service doc lists.
- Path traversal is blocked.
- No Caddy/DNS changes.
- Tests pass.
