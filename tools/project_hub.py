"""
Project Hub user tools: read and change the user's Kanban boards (projects -> columns -> tasks)
through the Project Hub HTTP API (see project-hub/docs/API.md).

Configuration:
  PROJECT_HUB_URL    base URL of the hub, e.g. http://localhost:8000/. Otherwise read from
                     ~/.config/project-hub/url, which project_hub_login(url=...) writes.
  PROJECT_HUB_TOKEN  API token; otherwise read from ~/.config/project-hub/token (shared with the
                     reference client). Get one with the project_hub_login tool.

Every change is GET all projects -> modify in memory -> POST the whole list, serialized by a lock.
"""

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from ai.tools import ToolCategory, tool

_CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "project-hub"
_TOKEN_FILE = _CONFIG_DIR / "token"
_URL_FILE = _CONFIG_DIR / "url"
_PENDING_FILE = _CONFIG_DIR / "pending_device_code.json"
_WRITE_LOCK = threading.Lock()
_STATUSES = ("not_started", "active", "archived")
_RELOAD_HINT = "If the board is open in a browser, reload it before editing there, or the change may be overwritten."


# --- HTTP ---------------------------------------------------------------------------------------

class _ApiError(Exception):
    def __init__(self, status: int, body):
        self.status, self.body = status, body
        super().__init__(f"HTTP {status}: {body}")


class _NotConfigured(Exception):
    pass


def _normalize_url(url: str) -> str:
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"Not a valid Project Hub URL: '{url}'. It must start with http:// or https://.")
    return url.rstrip("/") + "/"


def _saved_url() -> str:
    return _URL_FILE.read_text().strip() if _URL_FILE.exists() else ""


def _base_url() -> str:
    url = os.getenv("PROJECT_HUB_URL", "").strip() or _saved_url()
    if not url:
        raise _NotConfigured()
    return _normalize_url(url)


def _write_private(path: Path, text: str) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _load_token() -> str:
    token = os.getenv("PROJECT_HUB_TOKEN", "").strip()
    if token:
        return token
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text().strip()
    return ""


def _request(method: str, path: str, body=None, auth: bool = True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_base_url() + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        token = _load_token()
        if not token:
            raise _ApiError(401, {"detail": "Not authenticated"})
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        raise _ApiError(e.code, payload) from None


def _error(e: Exception) -> dict:
    """Turns an exception into a FAILED result with advice the model can act on."""
    if isinstance(e, _NotConfigured):
        return {"status": "FAILED", "error": "The Project Hub URL is not set. Ask the user where their Project Hub "
                                             "runs and call project_hub_login with url=<that address>."}
    if isinstance(e, _ApiError):
        if e.status == 401:
            return {"status": "FAILED", "error": "Not logged in to Project Hub (token missing, invalid or revoked). "
                                                 "Call project_hub_login and ask the user to approve it in the browser."}
        if e.status == 422:
            details = e.body.get("detail") if isinstance(e.body, dict) else e.body
            return {"status": "FAILED", "error": f"The server rejected the data (nothing was changed): {details}"}
        detail = e.body.get("detail") or e.body.get("error") if isinstance(e.body, dict) else e.body
        return {"status": "FAILED", "error": f"Project Hub returned HTTP {e.status}: {detail}"}
    if isinstance(e, urllib.error.URLError):
        return {"status": "FAILED", "error": f"Cannot reach Project Hub at {_base_url()}: {e.reason}. "
                                             "Check that the server is running and the URL is right "
                                             "(change it with project_hub_login url=...)."}
    return {"status": "FAILED", "error": str(e)}


def _get_projects() -> list:
    return _request("GET", "api/projects")


def _save_projects(projects: list) -> None:
    _request("POST", "api/projects", projects)


def _modify(change) -> dict:
    """Fresh read-modify-write. `change(projects)` edits the list in place and returns the result dict."""
    with _WRITE_LOCK:
        try:
            projects = _get_projects()
            result = change(projects)
            if result.get("status") == "SUCCESS":
                _save_projects(projects)
                result.setdefault("note", _RELOAD_HINT)
            return result
        except _Lookup as e:
            return {"status": "FAILED", "error": str(e)}
        except Exception as e:
            return _error(e)


# --- Lookups ------------------------------------------------------------------------------------

class _Lookup(Exception):
    """A project, column, task or item that can't be resolved; the message tells the model what exists."""


def _find_project(projects: list, ref) -> dict:
    ref = str(ref or "").strip()
    if not ref:
        raise _Lookup("Missing 'project' (name or id).")
    for p in projects:
        if p["id"] == ref:
            return p
    key = ref.casefold()
    exact = [p for p in projects if p["name"].casefold() == key]
    if len(exact) == 1:
        return exact[0]
    partial = exact or [p for p in projects if key in p["name"].casefold()]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(f"'{p['name']}' (id {p['id']}, {p.get('status', 'active')})" for p in (partial or projects))
    if partial:
        raise _Lookup(f"'{ref}' matches several projects: {names}. Ask the user which one, then pass its id.")
    raise _Lookup(f"No project '{ref}'. Projects: {names or 'none'}.")


def _find_column(project: dict, ref) -> dict:
    ref = str(ref or "").strip()
    key = ref.casefold()
    for c in project["columns"]:
        if c["id"] == ref or c["title"].casefold() == key:
            return c
    titles = ", ".join(f"'{c['title']}'" for c in project["columns"])
    raise _Lookup(f"No column '{ref}' in '{project['name']}'. Columns: {titles}.")


def _find_task(project: dict, task_id):
    try:
        tid = int(str(task_id).strip())
    except (TypeError, ValueError):
        raise _Lookup(f"task_id must be the task's integer id (see get_project), got '{task_id}'.")
    for c in project["columns"]:
        for t in c["tasks"]:
            if t["id"] == tid:
                return c, t
    raise _Lookup(f"No task with id {tid} in '{project['name']}'. Call get_project to see the task ids.")


def _new_task_id(project: dict) -> int:
    used = {t["id"] for c in project["columns"] for t in c["tasks"]}
    tid = int(time.time() * 1000)
    while tid in used:
        tid += 1
    return tid


def _apply_status_rule(project: dict) -> None:
    if project.get("status") == "not_started" and any(c["tasks"] for c in project["columns"][1:]):
        project["status"] = "active"


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            return [str(v).strip() for v in json.loads(text) if str(v).strip()]
        except ValueError:
            pass
    return [line.strip(" -*\t") for line in text.splitlines() if line.strip(" -*\t")]


def _as_bool(value, default=False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "done", "completed")


def _task_view(t: dict) -> dict:
    view = {"id": t["id"], "content": t["content"]}
    for key in ("description", "notes", "icon", "color"):
        if t.get(key):
            view[key] = t[key]
    if t.get("checklist"):
        view["checklist"] = [f"[{'x' if i['completed'] else ' '}] {n}. {i['text']}"
                             for n, i in enumerate(t["checklist"])]
    return view


def _project_view(p: dict) -> dict:
    return {
        "id": p["id"], "name": p["name"], "status": p.get("status", "active"),
        "columns": [{"id": c["id"], "title": c["title"], "tasks": [_task_view(t) for t in c["tasks"]]}
                    for c in p["columns"]],
    }


# --- Tools --------------------------------------------------------------------------------------

@tool
def project_hub_login(**kwargs) -> dict:
    """
    Logs in to Project Hub (the user's Kanban boards) with a browser approval. Call it when another
    project hub tool says you are not logged in.
    First call: returns a URL and a code; show both to the user and ask them to approve in the
    browser. Then call this tool again (no arguments) to finish; repeat while it says 'pending'.

    Args:
        url (string, optional): Where the user's Project Hub runs, e.g. 'http://localhost:8000/'.
            Needed the first time; it is saved for later calls. Giving a new one logs out.

    Returns:
        state: 'logged_in', 'pending' (with url and user_code to show the user) or an error.
    """
    try:
        if kwargs.get("url"):
            url = _normalize_url(kwargs["url"])
            env_url = os.getenv("PROJECT_HUB_URL", "").strip()
            if env_url and _normalize_url(env_url) != url:
                return {"status": "FAILED", "error": f"PROJECT_HUB_URL is set to {env_url} and takes priority. "
                                                     "Ask the user to change or unset it."}
            if url != (_normalize_url(_saved_url()) if _saved_url() else ""):
                _write_private(_URL_FILE, url + "\n")
                # A token belongs to one hub: forget the old login.
                _TOKEN_FILE.unlink(missing_ok=True)
                _PENDING_FILE.unlink(missing_ok=True)
        base = _base_url()

        if _PENDING_FILE.exists():
            pending = json.loads(_PENDING_FILE.read_text())
            if time.time() < pending["expires_at"]:
                try:
                    token = _request("POST", "api/auth/device/token",
                                     {"device_code": pending["device_code"]}, auth=False)["access_token"]
                except _ApiError as e:
                    code = e.body.get("error") if isinstance(e.body, dict) else None
                    if code in ("authorization_pending", "slow_down"):
                        return {"status": "SUCCESS", "state": "pending", "url": pending["url"],
                                "user_code": pending["user_code"],
                                "message": "Not approved yet. Ask the user to open the URL, check the code and "
                                           "approve, then call project_hub_login again."}
                    _PENDING_FILE.unlink(missing_ok=True)
                    if code == "access_denied":
                        return {"status": "FAILED", "error": "The user denied the login in the browser."}
                    if code != "expired_token":
                        raise
                else:
                    _PENDING_FILE.unlink(missing_ok=True)
                    _write_private(_TOKEN_FILE, token)
                    me = _request("GET", "api/auth/me")
                    return {"status": "SUCCESS", "state": "logged_in", "username": me.get("username")}
            _PENDING_FILE.unlink(missing_ok=True)

        if _load_token():
            try:
                me = _request("GET", "api/auth/me")
                return {"status": "SUCCESS", "state": "logged_in", "username": me.get("username")}
            except _ApiError as e:
                if e.status != 401:
                    raise

        start = _request("POST", "api/auth/device/code",
                         {"client_name": f"JARVIS on {socket.gethostname()}"[:100]}, auth=False)
        url = start["verification_uri_complete"]
        if not url.startswith(base):
            # The hub doesn't know it sits behind a proxy path (e.g. /hub/): build the URL from ours.
            url = f"{base}?user_code={start['user_code']}"
        _write_private(_PENDING_FILE, json.dumps({"device_code": start["device_code"],
                                                  "user_code": start["user_code"], "url": url,
                                                  "expires_at": time.time() + start["expires_in"]}))
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return {"status": "SUCCESS", "state": "pending", "url": url,
                "user_code": start["user_code"],
                "message": "Show the user the URL and code, ask them to approve it in the browser, "
                           "then call project_hub_login again to finish."}
    except Exception as e:
        return _error(e)


@tool
def list_projects(**kwargs) -> dict:
    """
    Lists the user's Project Hub projects (Kanban boards) with their status and how many tasks each
    column has. Start here to find a project's name or id.

    Args:
        include_archived (bool, optional): Also list archived projects. Default false.

    Returns:
        projects: id, name, status and task counts per column.
    """
    try:
        include_archived = _as_bool(kwargs.get("include_archived"))
        projects = [{"id": p["id"], "name": p["name"], "status": p.get("status", "active"),
                     "columns": {c["title"]: len(c["tasks"]) for c in p["columns"]}}
                    for p in _get_projects() if include_archived or p.get("status") != "archived"]
        return {"status": "SUCCESS", "projects": projects}
    except Exception as e:
        return _error(e)


@tool
def get_project(**kwargs) -> dict:
    """
    Shows one Project Hub project with all its columns and tasks (task ids, titles, descriptions,
    checklists). Use it to get the task_id other tools need.

    Args:
        project (string): Project name (case-insensitive) or id, e.g. 'Website'.

    Returns:
        project: columns in order, each with its tasks; checklist items as '[x] index. text'.
    """
    try:
        return {"status": "SUCCESS", "project": _project_view(_find_project(_get_projects(), kwargs.get("project")))}
    except _Lookup as e:
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        return _error(e)


@tool
def create_project(**kwargs) -> dict:
    """
    Creates a new Project Hub project with the columns To Do, Doing and Done.

    Args:
        name (string): Project name, e.g. 'Website'.

    Returns:
        project: the new project's id and name.
    """
    name = str(kwargs.get("name") or "").strip()
    if not name:
        return {"status": "FAILED", "error": "Missing 'name'."}

    def change(projects):
        if any(p["name"].casefold() == name.casefold() for p in projects):
            return {"status": "FAILED", "error": f"A project named '{name}' already exists."}
        pid = str(int(time.time() * 1000))
        while any(p["id"] == pid for p in projects):
            pid = str(int(pid) + 1)
        projects.append({"id": pid, "name": name, "status": "not_started", "columns": [
            {"id": "todo", "title": "To Do", "tasks": []},
            {"id": "doing", "title": "Doing", "tasks": []},
            {"id": "done", "title": "Done", "tasks": []},
        ]})
        return {"status": "SUCCESS", "project": {"id": pid, "name": name}}

    return _modify(change)


@tool
def add_task(**kwargs) -> dict:
    """
    Adds a task (card) to a Project Hub project.

    Args:
        project (string): Project name or id.
        content (string): Task title shown on the card, e.g. 'Fix footer links'.
        column (string, optional): Column title or id, e.g. 'Doing'. Default: the first column.
        description (string, optional): Longer description.
        icon (string, optional): One emoji, e.g. 🐞 for a bug, 🚀 feature, 🧪 test, 📝 writing.
        color (string, optional): green, yellow, orange, red, purple, blue, neutral, or a CSS color.
        checklist (list, optional): Checklist item texts, e.g. ["Hero", "Features"].

    Returns:
        task_id: the new task's id.
    """
    content = str(kwargs.get("content") or "").strip()
    if not content:
        return {"status": "FAILED", "error": "Missing 'content' (the task title)."}

    def change(projects):
        p = _find_project(projects, kwargs.get("project"))
        if p.get("status") == "archived":
            return {"status": "FAILED", "error": f"'{p['name']}' is archived. Ask the user before adding tasks "
                                                 "to it (set_project_status to 'active' first)."}
        col = _find_column(p, kwargs["column"]) if kwargs.get("column") else p["columns"][0]
        items = _as_list(kwargs.get("checklist"))
        tid = _new_task_id(p)
        col["tasks"].append({
            "id": tid, "content": content, "description": str(kwargs.get("description") or ""), "notes": "",
            "color": _color(kwargs.get("color")), "icon": str(kwargs.get("icon") or ""),
            "checklistTitle": "Todos :", "checklist": [{"text": i, "completed": False} for i in items],
        })
        _apply_status_rule(p)
        return {"status": "SUCCESS", "task_id": tid, "column": col["title"], "project": p["name"]}

    return _modify(change)


_COLORS = {"green": "#61bd4f", "yellow": "#f2d600", "orange": "#ff9f1a", "red": "#eb5a46",
           "purple": "#c377e0", "blue": "#0079bf", "neutral": "#475569", "grey": "#475569", "gray": "#475569"}


def _color(value) -> str:
    text = str(value or "").strip()
    return _COLORS.get(text.lower(), text) or "#475569"


@tool
def update_task(**kwargs) -> dict:
    """
    Changes fields of an existing Project Hub task. Only the fields you pass are changed.

    Args:
        project (string): Project name or id.
        task_id (int): The task's id (from get_project).
        content (string, optional): New title.
        description (string, optional): New description (replaces the old one).
        notes (string, optional): New notes (replace the old ones).
        icon (string, optional): One emoji.
        color (string, optional): green, yellow, orange, red, purple, blue, neutral, or a CSS color.
        add_checklist_items (list, optional): Item texts to append to the checklist.

    Returns:
        task: the task after the change.
    """
    def change(projects):
        p = _find_project(projects, kwargs.get("project"))
        _, t = _find_task(p, kwargs.get("task_id"))
        changed = False
        for key in ("content", "description", "notes", "icon"):
            if kwargs.get(key) is not None:
                t[key] = str(kwargs[key])
                changed = True
        if kwargs.get("color"):
            t["color"] = _color(kwargs["color"])
            changed = True
        for text in _as_list(kwargs.get("add_checklist_items")):
            t.setdefault("checklist", []).append({"text": text, "completed": False})
            t["checklistTitle"] = t.get("checklistTitle") or "Todos :"
            changed = True
        if not changed:
            return {"status": "FAILED", "error": "Nothing to change: pass at least one field."}
        if not t["content"].strip():
            return {"status": "FAILED", "error": "content can't be empty."}
        return {"status": "SUCCESS", "task": _task_view(t)}

    return _modify(change)


@tool
def move_task(**kwargs) -> dict:
    """
    Moves a Project Hub task to another column (e.g. to 'Done' when finished) or another position.

    Args:
        project (string): Project name or id.
        task_id (int): The task's id (from get_project).
        column (string): Target column title or id, e.g. 'Done'.
        position (int, optional): 0 = top of the column. Default: bottom.

    Returns:
        column: where the task is now.
    """
    def change(projects):
        p = _find_project(projects, kwargs.get("project"))
        src, t = _find_task(p, kwargs.get("task_id"))
        dst = _find_column(p, kwargs.get("column"))
        src["tasks"].remove(t)
        pos = kwargs.get("position")
        if pos is None or str(pos).strip() == "":
            dst["tasks"].append(t)
        else:
            dst["tasks"].insert(max(0, int(pos)), t)
        _apply_status_rule(p)
        return {"status": "SUCCESS", "task_id": t["id"], "from": src["title"], "column": dst["title"],
                "project_status": p.get("status")}

    return _modify(change)


@tool
def set_checklist_item(**kwargs) -> dict:
    """
    Ticks or unticks one checklist item of a Project Hub task.

    Args:
        project (string): Project name or id.
        task_id (int): The task's id (from get_project).
        item (string): The item's index (0 = first, as shown by get_project) or its text.
        completed (bool, optional): true to tick, false to untick. Default true.

    Returns:
        checklist: the task's checklist after the change.
    """
    def change(projects):
        p = _find_project(projects, kwargs.get("project"))
        _, t = _find_task(p, kwargs.get("task_id"))
        items = t.get("checklist") or []
        if not items:
            return {"status": "FAILED", "error": "This task has no checklist."}
        ref = str(kwargs.get("item", kwargs.get("index", kwargs.get("text", ""))) or "").strip()
        target = None
        if ref.isdigit() and int(ref) < len(items):
            target = items[int(ref)]
        else:
            matches = [i for i in items if i["text"].casefold() == ref.casefold()] or \
                      [i for i in items if ref and ref.casefold() in i["text"].casefold()]
            if len(matches) == 1:
                target = matches[0]
        if target is None:
            listing = "; ".join(f"{n}. {i['text']}" for n, i in enumerate(items))
            return {"status": "FAILED", "error": f"No single checklist item matches '{ref}'. Items: {listing}."}
        target["completed"] = _as_bool(kwargs.get("completed"), default=True)
        return {"status": "SUCCESS", "checklist": _task_view(t)["checklist"]}

    return _modify(change)


@tool
def delete_task(**kwargs) -> dict:
    """
    Deletes a Project Hub task for good. Ask the user to confirm before calling it.

    Args:
        project (string): Project name or id.
        task_id (int): The task's id (from get_project).

    Returns:
        deleted: the deleted task's title.
    """
    def change(projects):
        p = _find_project(projects, kwargs.get("project"))
        col, t = _find_task(p, kwargs.get("task_id"))
        col["tasks"].remove(t)
        return {"status": "SUCCESS", "deleted": t["content"], "column": col["title"]}

    return _modify(change)


@tool
def set_project_status(**kwargs) -> dict:
    """
    Sets a Project Hub project's status: 'archived' hides it from the main list, 'active' restores it.

    Args:
        project (string): Project name or id.
        status (string): 'not_started', 'active' or 'archived'.

    Returns:
        project: name and new status.
    """
    status = str(kwargs.get("status") or "").strip().lower()
    if status not in _STATUSES:
        return {"status": "FAILED", "error": f"status must be one of {', '.join(_STATUSES)}."}

    def change(projects):
        p = _find_project(projects, kwargs.get("project"))
        p["status"] = status
        return {"status": "SUCCESS", "project": p["name"], "project_status": status}

    return _modify(change)


class ProjectHubTools(ToolCategory):
    name = "project_hub"
    description = "The user's Project Hub Kanban boards: list projects, add, edit, move and tick off tasks."

    def get_category_tools(self):
        return [project_hub_login, list_projects, get_project, create_project, add_task, update_task,
                move_task, set_checklist_item, delete_task, set_project_status]
