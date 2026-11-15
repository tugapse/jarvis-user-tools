# jarvis-user-tools

My own tools for [JARVIS AI](https://github.com/tugapse/ai), my personal assistant.

---

## Why

JARVIS ships with the tools everyone needs: files, search, shell, web, notifications, knowledge,
memory. Everything else is personal. When I want JARVIS to do something for *me*, like move a card on
my Kanban board, I write a user tool for it.

Those tools **aren't part of JARVIS** and don't ship with it. They're built for my setup and my
servers, and they change whenever I need something new. They live here so every machine I use has
the same ones, and they're public in case they're useful as examples, or as tools, for someone else.

## How JARVIS uses them

A user tool is a Python file in `~/Ai/user/tools/` with `@tool` functions grouped in a
`ToolCategory`. The model only sees the docstring, so it's written as instructions for the model.
How to write one: [Writing your own tools](https://github.com/tugapse/ai/blob/master/guide/04-tools.md#writing-your-own-tools).

## Install

I keep this repo next to JARVIS and link the files, so a `git pull` updates the tools:

```bash
git clone https://github.com/tugapse/jarvis-user-tools ~/Code/jarvis-user-tools
mkdir -p ~/Ai/user/tools
ln -s ~/Code/jarvis-user-tools/tools/*.py ~/Ai/user/tools/
```

Copy the files instead if you want to change them.

Two things to know:
- JARVIS only gives an agent the tools its pipeline lists. Copy the pipeline (e.g. `default.json`)
  to `~/Ai/user/pipelines/` and add the tool names to the agent's `tools` list.
- Tools that change things should ask first: add them to `HIL_TOOLS` in `~/Ai/config.json`.

---

## Tools

### Project Hub (`tools/project_hub.py`)

Manages the Kanban boards of [Project Hub](https://github.com/tugapse/project-hub): projects,
tasks, checklists, columns and project status.

| Tool | What it does |
|---|---|
| `project_hub_login` | Logs in with a device code: the first call returns a URL and a code to open in the browser, the second call finishes. Pass `url=` the first time to say where your hub runs. |
| `list_projects` | Lists projects with their status and the number of tasks per column. |
| `get_project` | Shows one project's columns, tasks and checklists. |
| `create_project` | Creates a project with the default columns. |
| `add_task` | Adds a task (title, description, icon, color, checklist) to a column. |
| `update_task` | Changes a task's title, description, notes, icon or color, or adds checklist items. |
| `move_task` | Moves a task to another column or position. |
| `set_checklist_item` | Ticks or unticks a checklist item. |
| `delete_task` | Deletes a task. |
| `set_project_status` | Sets a project to `not_started`, `active` or `archived`. |

Projects can be named by id, by exact name or by part of the name (case-insensitive).

Config:

- Hub address: the first time, call `project_hub_login` with `url=` (e.g. `http://localhost:8000/`).
  It is saved in `~/.config/project-hub/url`. `PROJECT_HUB_URL` overrides it. Giving a new
  address logs you out of the old one.
- Token: `PROJECT_HUB_TOKEN`, or the file `~/.config/project-hub/token` that
  `project_hub_login` writes.

Every change reads all projects, changes them and saves the whole list. If the board is open in
a browser, reload it before editing there, or the browser's next save will undo the tool's
change.

Suggested `HIL_TOOLS`: `delete_task`, `set_project_status`.

## License

MIT

## Contact

**Maintainer:** Fabio Almeida · tugapse@gmail.com
