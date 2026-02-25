#!/usr/bin/env python3

import sys
import os
import json
import time
import re
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FEISHU_FILE = os.path.join(SCRIPT_DIR, "..", "assets", ".feishu")
TOKEN_CACHE = os.path.join(SCRIPT_DIR, "..", "assets", ".token_cache")
USER_TOKEN_CACHE = os.path.join(SCRIPT_DIR, "..", "assets", ".user_token_cache")
API_BASE = "https://open.feishu.cn/open-apis"


def usage():
    print(f"用法: {sys.argv[0]} <action> <Feishu_URL> [content_file]")
    print()
    print("  action       操作类型：read | write | append | clear")
    print("  Feishu_URL   飞书文档地址，如 https://xxx.feishu.cn/wiki/TOKEN")
    print("  content_file 写入时的内容文件路径（write 模式必填）")
    print()
    print("认证方式（优先级从高到低）：")
    print("  1. user_access_token：先运行 login.py 授权")
    print("  2. tenant_access_token：在 ../assets/.feishu 配置 app_id + app_secret")
    sys.exit(1)


# 读取配置（环境变量优先，.feishu 文件兜底）
def get_config(key):
    env_map = {"app_id": "FEISHU_APP_ID", "app_secret": "FEISHU_APP_SECRET"}
    env_val = os.environ.get(env_map.get(key, ""), "")
    if env_val:
        return env_val
    if not os.path.isfile(FEISHU_FILE):
        return ""
    with open(FEISHU_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == key:
                    return v
    return ""


# 获取 tenant_access_token（带缓存，2小时有效）
def get_access_token(app_id, app_secret):
    # 检查缓存是否有效（1.5小时内）
    if os.path.isfile(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, "r") as f:
                lines = f.read().strip().split("\n")
            if len(lines) >= 2:
                cached_time = int(lines[0])
                cached_token = lines[1]
                if time.time() - cached_time < 5400 and cached_token:
                    return cached_token
        except Exception:
            pass

    # 请求新 token
    req = urllib.request.Request(
        f"{API_BASE}/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"❌ 获取 tenant_access_token 失败: {e}", file=sys.stderr)
        return ""

    token = result.get("tenant_access_token", "")
    if not token:
        print(f"❌ 获取 tenant_access_token 失败: {result}", file=sys.stderr)
        return ""

    # 缓存 token
    with open(TOKEN_CACHE, "w") as f:
        f.write(f"{int(time.time())}\n{token}\n")
    return token


# 获取 user_access_token（从缓存读取，过期自动用 refresh_token 刷新）
def get_user_access_token(app_id, app_secret):
    if not os.path.isfile(USER_TOKEN_CACHE):
        return ""

    with open(USER_TOKEN_CACHE, "r") as f:
        cache = json.loads(f.read())

    access_token = cache.get("access_token", "")
    refresh_token = cache.get("refresh_token", "")
    expires_at = cache.get("expires_at", 0)

    # token 未过期，直接返回（提前5分钟刷新）
    if access_token and time.time() < expires_at - 300:
        return access_token

    # token 过期，用 refresh_token 刷新
    if not refresh_token:
        print("❌ refresh_token 为空，请重新运行 login.py", file=sys.stderr)
        return ""

    # 先获取 app_access_token
    req0 = urllib.request.Request(
        f"{API_BASE}/auth/v3/app_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
        method="POST",
    )
    req0.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req0) as resp0:
            app_token = json.loads(resp0.read().decode("utf-8")).get("app_access_token", "")
    except Exception:
        print("❌ 获取 app_access_token 失败", file=sys.stderr)
        return ""

    if not app_token:
        return ""

    # 刷新 user_access_token
    req = urllib.request.Request(
        f"{API_BASE}/authen/v1/oidc/refresh_access_token",
        data=json.dumps({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {app_token}")

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        print("❌ 刷新 token 失败，请重新运行 login.py", file=sys.stderr)
        return ""

    if result.get("code", -1) != 0:
        print(f"❌ 刷新 token 失败: {result.get('msg', '')}，请重新运行 login.py", file=sys.stderr)
        return ""

    data = result.get("data", {})
    new_access_token = data.get("access_token", "")
    new_refresh_token = data.get("refresh_token", "")
    new_expires_in = data.get("expires_in", 0)

    if not new_access_token:
        return ""

    # 更新缓存
    cache["access_token"] = new_access_token
    cache["refresh_token"] = new_refresh_token
    cache["expires_at"] = int(time.time()) + new_expires_in
    with open(USER_TOKEN_CACHE, "w") as f:
        json.dump(cache, f, indent=2)

    return new_access_token


# 解析飞书 URL
def parse_feishu_url(url):
    if not url:
        return None
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    m = re.match(r"(https?://[^/]+)/([^/]+)/([a-zA-Z0-9_-]+)", url)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


# 调用飞书 Open API
def api_call(method, path, access_token, body=None, retries=3):
    url = f"{API_BASE}{path}"
    for attempt in range(retries):
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("code") == 429 and attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return json.loads(error_body)
            except Exception:
                return {"code": e.code, "msg": error_body}
    return {"code": 429, "msg": "rate limited after retries"}


def check_resp(resp, action_name, auto_retry_login=False):
    code = resp.get("code", -1)
    if code != 0:
        msg = resp.get("msg") or resp.get("message") or "未知错误"
        
        # Token 过期或无效，尝试自动登录
        if code in (99991663, 99991664) and auto_retry_login:
            print(f"🔑 检测到 Token 问题 (code={code})，自动启动登录流程...", file=sys.stderr)
            print("", file=sys.stderr)
            import subprocess
            login_script = os.path.join(SCRIPT_DIR, "login.py")
            try:
                # 先退出登录清除旧 token
                subprocess.run(["python3", login_script, "logout"], check=False, capture_output=True)
                # 启动登录流程（会打开浏览器）
                result = subprocess.run(["python3", login_script], check=True, capture_output=False)
                if result.returncode == 0:
                    print("", file=sys.stderr)
                    print("✅ 登录完成，请重新执行命令", file=sys.stderr)
                    sys.exit(0)
            except subprocess.CalledProcessError:
                print("❌ 自动登录失败，请手动运行: python3 scripts/login.py", file=sys.stderr)
                sys.exit(1)
        
        print(f"❌ {action_name}失败 (code={code}): {msg}", file=sys.stderr)
        print("", file=sys.stderr)
        if code in (99991668, 99991672, 99991679, 1770032):
            print("📋 权限不足，请按以下步骤排查：", file=sys.stderr)
            print("", file=sys.stderr)
            print("   1️⃣  确认飞书应用已开通权限", file=sys.stderr)
            print("      打开 https://open.feishu.cn/app → 进入应用 → 权限管理", file=sys.stderr)
            print("      搜索并开通: docx:document + docx:document:readonly", file=sys.stderr)
            print("", file=sys.stderr)
            print("   2️⃣  重新发布应用版本", file=sys.stderr)
            print("      版本管理与发布 → 创建版本 → 提交发布", file=sys.stderr)
            print("      ⚠️ 每次改权限后都要重新发布，否则不生效", file=sys.stderr)
            print("", file=sys.stderr)
            print("   3️⃣  重新授权登录", file=sys.stderr)
            print("      python3 scripts/login.py logout && python3 scripts/login.py", file=sys.stderr)
            print("", file=sys.stderr)
            print("   如果仍然失败，确认你对该文档有编辑权限（飞书中能正常打开和编辑）", file=sys.stderr)
        elif code == 99991663:
            print("🔑 Token 已过期，请重新登录：", file=sys.stderr)
            print("   python3 scripts/login.py logout && python3 scripts/login.py", file=sys.stderr)
        elif code == 99991664:
            print("� Token 无效，可能未登录或缓存损坏，请重新登录：", file=sys.stderr)
            print("   python3 scripts/login.py logout && python3 scripts/login.py", file=sys.stderr)
        else:
            print("💡 排查建议：", file=sys.stderr)
            print("   1. 确认已运行 login.py 完成授权登录", file=sys.stderr)
            print("   2. 确认飞书应用权限已开通并发布", file=sys.stderr)
            print("   3. 重新登录: python3 scripts/login.py logout && python3 scripts/login.py", file=sys.stderr)
        print("", file=sys.stderr)
        print("📖 完整配置指南: https://github.com/hanhx/feishu-doc#readme", file=sys.stderr)
        sys.exit(1)
    return resp.get("data", {})


def extract_text(elements):
    if not elements:
        return ""
    parts = []
    for el in elements:
        if isinstance(el, dict):
            tr = el.get("text_run") or {}
            parts.append(tr.get("content", ""))
            mr = el.get("mention_user") or el.get("mention_doc") or {}
            if mr:
                parts.append(mr.get("content", ""))
    return "".join(parts)


def extract_block_text(block):
    for key in block:
        if isinstance(block[key], dict) and "elements" in block[key]:
            return extract_text(block[key].get("elements", []))
    return ""


def get_block_text_by_id(block_id, block_map, visited=None):
    if visited is None:
        visited = set()
    if not block_id or block_id in visited:
        return ""
    visited.add(block_id)

    block = block_map.get(block_id, {})
    if not block:
        return ""

    # 优先取当前块文本；无文本时递归拼接子块文本
    text = extract_block_text(block).strip()
    if text:
        return text

    child_texts = []
    for child_id in block.get("children", []) or []:
        child_text = get_block_text_by_id(child_id, block_map, visited)
        if child_text:
            child_texts.append(child_text)
    return "\n".join(child_texts)


def collect_descendant_ids(block_id, block_map, visited=None):
    if visited is None:
        visited = set()
    if not block_id or block_id in visited:
        return set()
    visited.add(block_id)

    block = block_map.get(block_id, {})
    descendants = set()
    for child_id in block.get("children", []) or []:
        descendants.add(child_id)
        descendants.update(collect_descendant_ids(child_id, block_map, visited))
    return descendants


def table_block_to_md(block, block_map):
    table = block.get("table", {})
    prop = table.get("property", {}) if isinstance(table, dict) else {}

    row_size = int(prop.get("row_size", 0) or 0)
    col_size = int(prop.get("column_size", 0) or 0)
    cell_ids = table.get("cells", []) if isinstance(table, dict) else []

    if row_size <= 0 or col_size <= 0 or not cell_ids:
        return "[表格]"

    row_count = min(row_size, max(1, len(cell_ids) // col_size))
    matrix = [["" for _ in range(col_size)] for _ in range(row_count)]

    total_cells = min(len(cell_ids), row_count * col_size)
    for idx in range(total_cells):
        r = idx // col_size
        c = idx % col_size
        text = get_block_text_by_id(cell_ids[idx], block_map).strip()
        text = text.replace("\n", "<br>").replace("|", "\\|")
        matrix[r][c] = text

    if not matrix:
        return "[表格]"

    header = matrix[0]
    separator = ["---"] * col_size
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in matrix[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def callout_block_to_md(block, block_map):
    texts = []

    # 优先从子块读取（避免与 elements 重复）
    children = block.get("children", []) or []
    if children:
        for child_id in children:
            child_text = get_block_text_by_id(child_id, block_map).strip()
            if child_text:
                texts.append(child_text)
    else:
        # 无子块时才从 callout.elements 读取
        callout = block.get("callout", {})
        if isinstance(callout, dict) and callout.get("elements"):
            direct = extract_text(callout.get("elements", [])).strip()
            if direct:
                texts.append(direct)

    if not texts:
        return None

    merged = "\n".join(texts)
    return "\n".join([f"> {ln}" if ln else ">" for ln in merged.split("\n")])


def block_to_md(block, block_map=None):
    btype = block.get("block_type", 0)
    if btype == 1:  # page
        page = block.get("page", {})
        return "# " + extract_text(page.get("elements", []))
    elif btype == 2:  # text
        return extract_text(block.get("text", {}).get("elements", []))
    elif btype in range(3, 12):  # heading 1-9
        level = btype - 2
        key = f"heading{level}"
        return "#" * level + " " + extract_text(block.get(key, {}).get("elements", []))
    elif btype == 12:  # bullet
        return "- " + extract_text(block.get("bullet", {}).get("elements", []))
    elif btype == 13:  # ordered
        return "1. " + extract_text(block.get("ordered", {}).get("elements", []))
    elif btype == 14:  # code
        code = block.get("code", {})
        lang_map = {
            0: "PlainText", 1: "ABAP", 2: "Ada", 3: "Apache", 4: "Apex", 5: "Assembly",
            6: "Bash", 7: "CSharp", 8: "CPP", 9: "C", 10: "COBOL", 11: "CSS", 12: "CoffeeScript",
            13: "D", 14: "Dart", 15: "Delphi", 16: "Django", 17: "Dockerfile", 18: "Erlang",
            19: "Fortran", 20: "FoxPro", 21: "Go", 22: "Groovy", 23: "HTML", 24: "HTMLBars",
            25: "HTTP", 26: "Haskell", 27: "JSON", 28: "Java", 29: "JavaScript", 30: "Julia",
            31: "Kotlin", 32: "LateX", 33: "Lisp", 34: "Logo", 35: "Lua", 36: "MATLAB",
            37: "Makefile", 38: "Markdown", 39: "Nginx", 40: "Objective-C", 41: "OpenEdgeABL",
            42: "PHP", 43: "Perl", 44: "PostScript", 45: "Power Shell", 46: "Prolog",
            47: "ProtoBuf", 48: "Python", 49: "R", 50: "RPG", 51: "Ruby", 52: "Rust", 53: "SAS",
            54: "SCSS", 55: "SQL", 56: "Scala", 57: "Scheme", 58: "Scratch", 59: "Shell",
            60: "Swift", 61: "Thrift", 62: "TypeScript", 63: "VBScript", 64: "Visual Basic",
            65: "XML", 66: "YAML",
        }
        lang = lang_map.get(code.get("style", {}).get("language", 0), "")
        return f"```{lang}\n{extract_text(code.get('elements', []))}\n```"
    elif btype == 15:  # quote
        return "> " + extract_text(
            block.get("quote_container", block.get("quote", {})).get("elements", [])
        )
    elif btype == 17:  # todo
        todo = block.get("todo", {})
        done = todo.get("style", {}).get("done", False)
        return f"- [{'x' if done else ' '}] " + extract_text(todo.get("elements", []))
    elif btype == 23:  # divider
        return "---"
    elif btype == 27:  # image
        return "[图片]"
    elif btype == 22 or (btype == 31 and isinstance(block.get("table"), dict)):  # table
        return table_block_to_md(block, block_map or {})
    elif btype == 18:  # bitable
        return "[多维表格]"
    elif btype == 31:  # grid
        return "[分栏]"
    elif btype == 19:  # callout
        return callout_block_to_md(block, block_map or {})
    else:
        return extract_block_text(block)


def parse_inline_styles(text):
    """Parse markdown inline styles into feishu text_run elements with styles."""
    if not text:
        return [{"text_run": {"content": " "}}]
    elements = []
    pattern = re.compile(
        r'(\*\*(.+?)\*\*)'           # bold
        r'|(`([^`]+)`)'              # inline code
        r'|(~~(.+?)~~)'              # strikethrough
        r'|(\[([^\]]+)\]\(([^)]+)\))'  # link
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            elements.append({"text_run": {"content": text[pos:m.start()]}})
        if m.group(2):  # bold
            elements.append({"text_run": {"content": m.group(2), "text_element_style": {"bold": True}}})
        elif m.group(4):  # inline code
            elements.append({"text_run": {"content": m.group(4), "text_element_style": {"inline_code": True}}})
        elif m.group(6):  # strikethrough
            elements.append({"text_run": {"content": m.group(6), "text_element_style": {"strikethrough": True}}})
        elif m.group(8):  # link
            link_url = m.group(9)
            if link_url.startswith("http://") or link_url.startswith("https://"):
                elements.append({"text_run": {"content": m.group(8), "text_element_style": {"link": {"url": link_url}}}})
            else:
                elements.append({"text_run": {"content": f"[{m.group(8)}]({link_url})"}})
        pos = m.end()
    if pos < len(text):
        elements.append({"text_run": {"content": text[pos:]}})
    return elements if elements else [{"text_run": {"content": " "}}]


def make_text_elements(text):
    return parse_inline_styles(text)


def make_plain_elements(text):
    return [{"text_run": {"content": text}}] if text else [{"text_run": {"content": " "}}]


def make_text_block(text):
    return {"block_type": 2, "text": {"elements": make_text_elements(text)}}


def make_heading_block(level, text):
    level = max(1, min(level, 9))
    block_type = level + 2  # H1=3, H2=4, ..., H9=11
    key = f"heading{level}"
    elements = [{"text_run": {"content": text, "text_element_style": {"bold": True}}}]
    return {"block_type": block_type, key: {"elements": elements}}


def make_bullet_block(text):
    return {"block_type": 12, "bullet": {"elements": make_text_elements(text)}}


def make_ordered_block(text):
    return {"block_type": 13, "ordered": {"elements": make_text_elements(text)}}


def make_code_block(code_text, lang=""):
    lang_map = {
        "sql": 56, "java": 29, "javascript": 30, "typescript": 63, "python": 49,
        "go": 22, "bash": 7, "shell": 60, "json": 28, "yaml": 67, "xml": 66,
        "html": 24, "css": 11, "groovy": 23, "lua": 36, "markdown": 39,
        "nginx": 40, "php": 43, "c": 10, "cpp": 9, "c++": 9, "csharp": 8, "c#": 8,
        "scala": 57, "ruby": 52, "rust": 53, "r": 50, "scss": 55,
        "mermaid": 21, "plaintext": 21, "": 21,
    }
    lang_code = lang_map.get(lang.lower(), 21)
    return {
        "block_type": 14,
        "code": {
            "elements": make_plain_elements(code_text),
            "style": {"language": lang_code},
        },
    }


def make_quote_block(text):
    return {"_callout": True, "_callout_text": text}


def make_divider_block():
    return {"block_type": 2, "text": {"elements": make_text_elements("───────────────────")}}


def make_todo_block(text, done=False):
    return {
        "block_type": 17,
        "todo": {
            "elements": make_text_elements(text),
            "style": {"done": done},
        },
    }


def process(action, doc_url, access_token, doc_type, token, content_file=""):
    doc_token = token

    if action == "read":
        # 获取纯文本
        resp = api_call("GET", f"/docx/v1/documents/{doc_token}/raw_content", access_token)
        data = check_resp(resp, "获取文档内容", auto_retry_login=True)
        content = data.get("content", "")

        # 获取 blocks 并转为 markdown（支持翻页）
        items = []
        page_token = ""
        while True:
            url = f"/docx/v1/documents/{doc_token}/blocks?page_size=500"
            if page_token:
                url += f"&page_token={page_token}"
            resp2 = api_call("GET", url, access_token)
            blocks_data = resp2.get("data", {}) if resp2.get("code", -1) == 0 else {}
            items.extend(blocks_data.get("items", []))
            if not blocks_data.get("has_more", False):
                break
            page_token = blocks_data.get("page_token", "")
            if not page_token:
                break

        block_map = {it.get("block_id"): it for it in items if it.get("block_id")}
        skip_block_ids = set()
        for it in items:
            btype = it.get("block_type", 0)
            # Skip table cell descendants
            is_table = btype == 22 or (btype == 31 and isinstance(it.get("table"), dict))
            if is_table:
                table = it.get("table", {})
                for cell_id in table.get("cells", []) or []:
                    skip_block_ids.add(cell_id)
                    skip_block_ids.update(collect_descendant_ids(cell_id, block_map))
            # Skip callout children to avoid duplication
            if btype == 19:
                for child_id in it.get("children", []) or []:
                    skip_block_ids.add(child_id)
                    skip_block_ids.update(collect_descendant_ids(child_id, block_map))

        md_lines = []
        for item in items:
            block_id = item.get("block_id", "")
            if block_id and block_id in skip_block_ids:
                continue
            line = block_to_md(item, block_map)
            if line is not None:
                md_lines.append(line)

        markdown = "\n".join(md_lines)
        title = ""

        out = {
            "docUrl": doc_url,
            "title": title if doc_type == "wiki" else "",
            "blockCount": len(items),
            "markdown": markdown,
            "rawContent": content,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))

    elif action == "clear":
        page_block_id = doc_token
        clear_resp = api_call("GET", f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}", access_token)
        clear_data = check_resp(clear_resp, "获取文档块", auto_retry_login=True)
        clear_children = clear_data.get("block", {}).get("children", [])
        # 清空标题
        api_call(
            "PATCH",
            f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}",
            access_token,
            {"update_text_elements": {"elements": [{"text_run": {"content": " "}}]}},
        )
        if not clear_children:
            out = {"docUrl": doc_url, "action": "clear", "blocksDeleted": 0, "status": "success"}
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            del_count = len(clear_children)
            del_resp = api_call(
                "DELETE",
                f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}/children/batch_delete",
                access_token,
                {"start_index": 0, "end_index": del_count},
            )
            if del_resp.get("code") != 0:
                remaining = del_count
                while remaining > 0:
                    batch = min(50, remaining)
                    api_call(
                        "DELETE",
                        f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}/children/batch_delete",
                        access_token,
                        {"start_index": 0, "end_index": batch},
                    )
                    remaining -= batch
                    time.sleep(0.3)
            out = {"docUrl": doc_url, "action": "clear", "blocksDeleted": del_count, "status": "success"}
            print(json.dumps(out, ensure_ascii=False, indent=2))

    elif action in ("write", "append"):
        if not content_file:
            print(f"❌ {action} 模式需要指定内容文件路径", file=sys.stderr)
            sys.exit(1)

        with open(content_file, "r", encoding="utf-8") as f:
            content = f.read()

        page_block_id = doc_token
        BATCH_SIZE = 50
        counter = [0]

        def flush_blocks(block_list):
            pending_buf = []
            for blk in block_list:
                if blk.get("_callout"):
                    while pending_buf:
                        batch = pending_buf[:BATCH_SIZE]
                        pending_buf = pending_buf[BATCH_SIZE:]
                        resp = api_call(
                            "POST",
                            f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}/children",
                            access_token,
                            {"children": batch, "index": -1},
                        )
                        check_resp(resp, "写入文档", auto_retry_login=True)
                        counter[0] += len(batch)
                        time.sleep(0.5)
                    cb = {"block_type": 19, "callout": {"background_color": 15}}
                    cr = api_call(
                        "POST",
                        f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}/children",
                        access_token,
                        {"children": [cb], "index": -1},
                    )
                    cd = check_resp(cr, "创建引用块", auto_retry_login=True)
                    counter[0] += 1
                    ci = cd.get("children", [{}])[0].get("block_id", "")
                    if ci:
                        cc = {"block_type": 2, "text": {"elements": make_text_elements(blk["_callout_text"])}}
                        api_call(
                            "POST",
                            f"/docx/v1/documents/{doc_token}/blocks/{ci}/children",
                            access_token,
                            {"children": [cc], "index": 0},
                        )
                    time.sleep(0.3)
                else:
                    pending_buf.append(blk)
            while pending_buf:
                batch = pending_buf[:BATCH_SIZE]
                pending_buf = pending_buf[BATCH_SIZE:]
                resp = api_call(
                    "POST",
                    f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}/children",
                    access_token,
                    {"children": batch, "index": -1},
                )
                check_resp(resp, "写入文档", auto_retry_login=True)
                counter[0] += len(batch)
                time.sleep(0.5)

        lines = content.split("\n")
        children = []
        doc_title_set = False
        i = 0

        while i < len(lines):
            line = lines[i]

            # 第一个 H1 标题 → 设置为文档标题（page block title），append 模式跳过
            if not doc_title_set and re.match(r"^#\s+(.+)", line) and not re.match(r"^##", line):
                title_text = re.match(r"^#\s+(.+)", line).group(1)
                if action == "write":
                    api_call(
                        "PATCH",
                        f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}",
                        access_token,
                        {"update_text_elements": {"elements": [{"text_run": {"content": title_text}}]}},
                    )
                else:
                    children.append(make_heading_block(1, title_text))
                doc_title_set = True
                i += 1
                continue

            # 代码块
            if line.strip().startswith("```"):
                lang = line.strip()[3:].strip()
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing ```
                code_text = "\n".join(code_lines)
                # 自动检测语言
                if not lang:
                    ct = code_text.strip()
                    if any(k in ct for k in ["CREATE TABLE", "ALTER TABLE", "INSERT INTO", "SELECT ", "DROP TABLE"]):
                        lang = "sql"
                    elif any(k in ct for k in ["@FeignClient", "public ", "private ", "interface ", "class ", "@Override", "@GetMapping", "@PostMapping", "import "]):
                        lang = "java"
                    elif ct.startswith("{") or ct.startswith("["):
                        lang = "json"
                    elif any(k in ct for k in ["flowchart", "sequenceDiagram", "stateDiagram", "erDiagram", "gantt"]):
                        lang = "mermaid"
                    elif any(k in ct for k in ["GET /", "POST /", "PUT /", "DELETE /"]):
                        lang = "bash"
                children.append(make_code_block(code_text, lang))
                continue

            # 空行 → 跳过
            if not line.strip():
                i += 1
                continue

            # 分割线
            if re.match(r"^-{3,}$", line.strip()) or re.match(r"^\*{3,}$", line.strip()):
                children.append(make_divider_block())
                i += 1
                continue

            # 标题
            hm = re.match(r"^(#{1,9})\s+(.*)", line)
            if hm:
                level = len(hm.group(1))
                children.append(make_heading_block(level, hm.group(2)))
                i += 1
                continue

            # 去掉前导空格用于匹配
            stripped = line.lstrip()

            # todo（支持缩进）
            tm = re.match(r"^-\s*\[([ xX])\]\s*(.*)", stripped)
            if tm:
                done = tm.group(1).lower() == "x"
                children.append(make_todo_block(tm.group(2), done))
                i += 1
                continue

            # 无序列表（支持缩进）
            if re.match(r"^[-*+]\s+", stripped):
                text = re.sub(r"^[-*+]\s+", "", stripped)
                children.append(make_bullet_block(text))
                i += 1
                continue

            # 有序列表（支持缩进）
            om = re.match(r"^\d+\.\s+(.*)", stripped)
            if om:
                children.append(make_ordered_block(om.group(1)))
                i += 1
                continue

            # 引用（合并连续 > 行）
            if stripped.startswith("> ") or stripped == ">" or (stripped.startswith(">") and not stripped.startswith(">" * 3)):
                quote_lines = []
                while i < len(lines):
                    ql = lines[i].lstrip()
                    if ql.startswith("> "):
                        ql = ql[2:]
                    elif ql.startswith(">"):
                        ql = ql[1:]
                    else:
                        break
                    quote_lines.append(ql)
                    i += 1
                children.append(make_quote_block("\n".join(quote_lines)))
                continue

            # 表格 → 飞书原生表格
            if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s\-:|]+\|?\s*$", lines[i + 1]):
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                if len(table_lines) >= 2:
                    header_cells = [c.strip() for c in table_lines[0].split("|") if c.strip()]
                    data_rows = []
                    for row_line in table_lines[2:]:
                        cells = [c.strip() for c in row_line.split("|") if c.strip()]
                        data_rows.append(cells)
                    col_size = len(header_cells)
                    row_size = 1 + len(data_rows)
                    # 计算列宽
                    all_rows_for_width = [header_cells] + data_rows
                    col_max_len = [0] * col_size
                    for row_cells in all_rows_for_width:
                        for ci in range(min(len(row_cells), col_size)):
                            col_max_len[ci] = max(col_max_len[ci], len(row_cells[ci]))
                    total_len = max(sum(col_max_len), 1)
                    total_width = 700
                    col_widths = [max(100, int(total_width * cl / total_len)) for cl in col_max_len]
                    # 先把当前 children 写入
                    flush_blocks(children)
                    children = []
                    # 大表格拆分：每个子表最多 8 行数据 + 1 行表头 = 9 行
                    MAX_DATA_ROWS = 8
                    from concurrent.futures import ThreadPoolExecutor

                    def create_and_fill_table(h_cells, d_rows, c_size, c_widths):
                        sub_row_size = 1 + len(d_rows)
                        tb = {
                            "block_type": 31,
                            "table": {
                                "property": {
                                    "row_size": sub_row_size,
                                    "column_size": c_size,
                                    "column_width": c_widths,
                                    "header_row": True,
                                },
                            },
                        }
                        tr = api_call(
                            "POST",
                            f"/docx/v1/documents/{doc_token}/blocks/{page_block_id}/children",
                            access_token,
                            {"children": [tb], "index": -1},
                        )
                        if tr.get("code", -1) != 0:
                            print(
                                f"⚠️ 表格创建失败({sub_row_size}x{c_size}), fallback: {tr.get('msg', '')[:80]}",
                                file=sys.stderr,
                            )
                            return False
                        counter[0] += 1
                        tc = tr.get("data", {}).get("children", [])
                        if tc:
                            cids = tc[0].get("table", {}).get("cells", [])
                            a_rows = [h_cells] + d_rows

                            def fill_cell(args):
                                cell_id, text, is_header = args
                                el = make_plain_elements(text) if is_header else make_text_elements(text)
                                cell_block = {"block_type": 2, "text": {"elements": el}}
                                api_call(
                                    "POST",
                                    f"/docx/v1/documents/{doc_token}/blocks/{cell_id}/children",
                                    access_token,
                                    {"children": [cell_block], "index": 0},
                                )

                            tasks = []
                            for ri, rc in enumerate(a_rows):
                                for ci2 in range(c_size):
                                    cidx = ri * c_size + ci2
                                    if cidx >= len(cids):
                                        break
                                    ct = rc[ci2] if ci2 < len(rc) else ""
                                    if not ct:
                                        continue
                                    tasks.append((cids[cidx], ct, ri == 0))
                            with ThreadPoolExecutor(max_workers=5) as pool:
                                pool.map(fill_cell, tasks)
                        time.sleep(0.5)
                        return True

                    # 拆分数据行
                    for chunk_start in range(0, len(data_rows), MAX_DATA_ROWS):
                        chunk = data_rows[chunk_start:chunk_start + MAX_DATA_ROWS]
                        if not create_and_fill_table(header_cells, chunk, col_size, col_widths):
                            # fallback: 整个表格用代码块
                            children.append(make_code_block("\n".join(table_lines), "markdown"))
                            break
                continue

            # 跳过空行（避免在引用块、表格后多出空白块）
            if not line.strip():
                i += 1
                continue

            # 普通文本
            children.append(make_text_block(line))
            i += 1

        if not children and counter[0] == 0:
            print("❌ 内容为空", file=sys.stderr)
            sys.exit(1)

        # 写入所有剩余 blocks
        flush_blocks(children)

        out = {
            "docUrl": doc_url,
            "action": "write",
            "blocksAdded": counter[0],
            "totalBatches": (len(children) + BATCH_SIZE - 1) // BATCH_SIZE,
            "status": "success",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))


# --- 主逻辑 ---
def main():
    if len(sys.argv) < 3:
        usage()

    action = sys.argv[1]
    doc_url = sys.argv[2]
    content_file = sys.argv[3] if len(sys.argv) > 3 else ""

    if not doc_url:
        usage()

    if action not in ("read", "write", "append", "clear"):
        print(f"❌ 不支持的操作: {action}，请使用 read / write / append / clear", file=sys.stderr)
        sys.exit(1)

    # 解析 URL
    parsed = parse_feishu_url(doc_url)
    if not parsed:
        print("❌ 请输入正确的飞书文档地址，格式示例：", file=sys.stderr)
        print("  https://xxx.feishu.cn/wiki/TOKEN", file=sys.stderr)
        print("  https://xxx.feishu.cn/docx/TOKEN", file=sys.stderr)
        sys.exit(1)

    domain, doc_type, token = parsed

    # 获取凭证
    app_id = get_config("app_id")
    app_secret = get_config("app_secret")

    # 必须使用 user_access_token（个人授权）
    if not app_id or not app_secret:
        print("❌ 未找到应用凭证，请先完成配置：", file=sys.stderr)
        print("", file=sys.stderr)
        print("   1️⃣  配置应用凭证（二选一）：", file=sys.stderr)
        print("      方式A: 环境变量（推荐）", file=sys.stderr)
        print("        export FEISHU_APP_ID=cli_xxxx", file=sys.stderr)
        print("        export FEISHU_APP_SECRET=xxxx", file=sys.stderr)
        print("      方式B: 编辑 assets/.feishu 文件", file=sys.stderr)
        print("        app_id=cli_xxxx", file=sys.stderr)
        print("        app_secret=xxxx", file=sys.stderr)
        print("", file=sys.stderr)
        print("   2️⃣  授权登录：", file=sys.stderr)
        print("      python3 scripts/login.py", file=sys.stderr)
        print("", file=sys.stderr)
        print("   💡 没有 App ID？参考: https://github.com/hanhx/feishu-doc#readme", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(USER_TOKEN_CACHE):
        print("🔑 检测到未登录，自动启动登录流程...", file=sys.stderr)
        print("", file=sys.stderr)
        import subprocess
        login_script = os.path.join(SCRIPT_DIR, "login.py")
        try:
            result = subprocess.run(["python3", login_script], check=True, capture_output=False)
            if result.returncode == 0:
                print("", file=sys.stderr)
                print("✅ 登录完成，请重新执行命令", file=sys.stderr)
                sys.exit(0)
        except subprocess.CalledProcessError:
            print("❌ 自动登录失败，请手动运行: python3 scripts/login.py", file=sys.stderr)
            sys.exit(1)

    access_token = get_user_access_token(app_id, app_secret)
    if not access_token:
        print("🔑 Token 获取失败，自动启动登录流程...", file=sys.stderr)
        print("", file=sys.stderr)
        import subprocess
        login_script = os.path.join(SCRIPT_DIR, "login.py")
        try:
            subprocess.run(["python3", login_script, "logout"], check=False, capture_output=True)
            result = subprocess.run(["python3", login_script], check=True, capture_output=False)
            if result.returncode == 0:
                print("", file=sys.stderr)
                print("✅ 登录完成，请重新执行命令", file=sys.stderr)
                sys.exit(0)
        except subprocess.CalledProcessError:
            print("❌ 自动登录失败，请手动运行: python3 scripts/login.py", file=sys.stderr)
            sys.exit(1)

    # 执行操作
    process(action, doc_url, access_token, doc_type, token, content_file)


if __name__ == "__main__":
    main()
