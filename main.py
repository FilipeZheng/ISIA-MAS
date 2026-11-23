"""Run the scenarios from scenarios.py and expose the environment in SPADE's web UI.

The environment grid is served by the operator agent's built-in SPADE web
interface (http://127.0.0.1:10000/spade/environment/). While a simulation is
running the page refreshes itself every refresh_interval seconds. From that page
you can preview the starting positions of a scenario, choose how ambulances are
selected, turn volunteer bidding and ambulance repositioning on or off, start
the selected scenario and stop the running simulation.

Run it with the virtual environment interpreter:

    spade_venv/bin/python main.py          (Linux/macOS)
    spade_venv\\Scripts\\python.exe main.py  (Windows)

If ``spade`` is not importable from the current interpreter, this script
re-executes itself with the interpreter from ``spade_venv`` automatically.
"""

import os
import sys

_VENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spade_venv")
_VENV_PYTHON = os.path.join(
    _VENV_DIR,
    "Scripts" if os.name == "nt" else "bin",
    "python.exe" if os.name == "nt" else "python",
)

try:
    import spade  # noqa: F401
except ModuleNotFoundError:
    if os.path.exists(_VENV_PYTHON) and os.path.abspath(sys.executable) != os.path.abspath(
        _VENV_PYTHON
    ):
        os.execv(_VENV_PYTHON, [_VENV_PYTHON, os.path.abspath(__file__), *sys.argv[1:]])
    raise

import asyncio
import html
import logging
import socket

import agents
import scenarios
from aiohttp import web as aioweb
from spade.web import unused_port

logger = logging.getLogger("P_web")

WEB_HOST = "127.0.0.1"
WEB_PORT = int(os.environ.get("SPADE_WEB_PORT", "10000"))

ENVIRONMENT_PATH = "/spade/environment/"
START_PATH = "/spade/environment/start/"
STOP_PATH = "/spade/environment/stop/"

# The page refreshes itself this often (seconds).
REFRESH_INTERVAL = "1"

# Label and colour used to draw each agent kind in a scenario preview.
PREVIEW_LABELS = {
    "person": ("P", "person"),
    "volunteer": ("V", "volunteer"),
    "ambulance": ("A", "ambulance"),
}


def _port_is_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _describe_cell(cell):
    """Return the (label, css class, tooltip) used to draw one live cell."""
    labels = []
    classes = []
    occupants = []

    for obj in cell:
        if isinstance(obj, agents.Hospital):
            labels.append("H")
            classes.append("hospital")
            occupants.append("hospital")
        elif isinstance(obj, agents.EmergencyAmbulance):
            labels.append("A")
            classes.append("ambulance")
            occupants.append(str(obj.jid))
        elif isinstance(obj, agents.VolunteerAgent):
            if obj.collapsed:
                labels.append("C")
                classes.append("collapsed")
            else:
                labels.append("V")
                classes.append("volunteer")
            occupants.append(f"{obj.jid}{' (collapsed)' if obj.collapsed else ''}")
        elif isinstance(obj, agents.WalkingAgent):
            if obj.collapsed:
                labels.append("C")
                classes.append("collapsed")
            else:
                labels.append("P")
                classes.append("person")
            occupants.append(f"{obj.jid}{' (collapsed)' if obj.collapsed else ''}")
        else:
            labels.append("?")
            classes.append("other")
            occupants.append(type(obj).__name__)

    if not labels:
        return "", "", ""

    # Keep a single letter per cell and let the count show extra occupants.
    label = labels[0]
    if len(labels) > 1:
        label = f"{label}{len(labels)}"

    return label, classes[0], ", ".join(occupants)


def _preview_layout(name):
    """Return the grid of (label, css class, tooltip) for a scenario preview."""
    width, height = agents.Environment_width, agents.Environment_height
    occupants = [[[] for _ in range(width)] for _ in range(height)]

    for placement in scenarios.scenario_placements(name):
        label, css_class = PREVIEW_LABELS[placement.kind]
        tooltip = placement.jid
        if placement.target is not None:
            tooltip += f" -> {placement.target}"
        occupants[placement.y][placement.x].append((label, css_class, tooltip))

    hospital_x, hospital_y = scenarios.HOSPITAL_POSITION
    occupants[hospital_y][hospital_x].append(("H", "hospital", "hospital"))

    layout = []
    for y in range(height):
        row = []
        for x in range(width):
            items = occupants[y][x]
            if not items:
                row.append(("", "", ""))
                continue
            label = items[0][0]
            if len(items) > 1:
                label = f"{label}{len(items)}"
            row.append((label, items[0][1], ", ".join(item[2] for item in items)))
        layout.append(row)
    return layout


def _live_counts():
    """Return (people, volunteers, ambulances, hospitals) in the environment."""
    people = volunteers = ambulances = hospitals = 0
    for row in agents.Environment:
        for cell in row:
            for obj in cell:
                if isinstance(obj, agents.Hospital):
                    hospitals += 1
                elif isinstance(obj, agents.EmergencyAmbulance):
                    ambulances += 1
                elif isinstance(obj, agents.VolunteerAgent):
                    volunteers += 1
                elif isinstance(obj, agents.WalkingAgent):
                    people += 1
    return people, volunteers, ambulances, hospitals


def _preview_counts(name):
    """Return the number of agents a scenario will place in the environment."""
    people = volunteers = ambulances = 0
    for placement in scenarios.scenario_placements(name):
        if placement.kind == "person":
            people += 1
        elif placement.kind == "volunteer":
            volunteers += 1
        elif placement.kind == "ambulance":
            ambulances += 1
    return people, volunteers, ambulances, 1  # one hospital


def _ambulance_mode_select(selected):
    """Return the ambulance selection <select> used by the start form."""
    options = "".join(
        '<option value="{value}"{active}>{title}</option>'.format(
            value=html.escape(mode, quote=True),
            active=" selected" if mode == selected else "",
            title=html.escape(title),
        )
        for mode, title in agents.AMBULANCE_MODES.items()
    )
    return (
        '<label class="mode">Ambulance selection:'
        '<select name="ambulance_mode">{options}</select></label>'
    ).format(options=options)


def _hidden_inputs(selected, **values):
    """Return hidden inputs that preserve the current page selections."""
    hidden = ""
    if selected is not None:
        hidden += '<input type="hidden" name="scenario" value="{name}">'.format(
            name=html.escape(selected, quote=True)
        )
    for name, value in values.items():
        hidden += '<input type="hidden" name="{name}" value="{value}">'.format(
            name=html.escape(name, quote=True),
            value=html.escape(value, quote=True),
        )
    return hidden


def _toggle_form(name, value, label, active, hidden):
    """Return a GET form with one toggle button."""
    return (
        '<form class="mode-toggle" method="get" action="{path}">'
        "{hidden}"
        '<button class="scenario toggle{active}" type="submit" '
        'name="{name}" value="{value}">{label}</button>'
        "</form>"
    ).format(
        path=ENVIRONMENT_PATH,
        hidden=hidden,
        active=" selected" if active else "",
        name=html.escape(name, quote=True),
        value=html.escape(value, quote=True),
        label=html.escape(label),
    )


def _volunteer_mode_button(selected, volunteer_mode, ambulance_mode,
                           ambulance_reposition):
    """Return the button that toggles whether volunteers bid for incidents."""
    bidding = volunteer_mode == agents.VOLUNTEER_MODE_BIDDING
    next_mode = (
        agents.VOLUNTEER_MODE_CLOSEST if bidding else agents.VOLUNTEER_MODE_BIDDING
    )
    state = "on" if bidding else "off"
    action = "turn off" if bidding else "turn on"
    hidden = _hidden_inputs(
        selected,
        ambulance_mode=ambulance_mode,
        ambulance_reposition=ambulance_reposition,
    )
    return _toggle_form(
        "volunteer_mode",
        next_mode,
        f"Volunteer bidding: {state} ({action})",
        bidding,
        hidden,
    )


def _ambulance_reposition_button(selected, ambulance_mode, volunteer_mode,
                                 ambulance_reposition):
    """Return the button that toggles ambulance repositioning."""
    enabled = ambulance_reposition == agents.AMBULANCE_REPOSITION_ON
    next_mode = (
        agents.AMBULANCE_REPOSITION_OFF if enabled
        else agents.AMBULANCE_REPOSITION_ON
    )
    state = "on" if enabled else "off"
    action = "turn off" if enabled else "turn on"
    hidden = _hidden_inputs(
        selected,
        ambulance_mode=ambulance_mode,
        volunteer_mode=volunteer_mode,
    )
    return _toggle_form(
        "ambulance_reposition",
        next_mode,
        f"Ambulance repositioning: {state} ({action})",
        enabled,
        hidden,
    )


def _scenario_controls(selected, ambulance_mode, volunteer_mode,
                       ambulance_reposition):
    """Return the scenario buttons and, when previewing, the start button."""
    if scenarios.simulation_running:
        return (
            '<form method="post" action="{path}">'
            '<button class="stop" type="submit">Stop simulation</button>'
            "</form>"
            '<p class="hint">Ambulance selection: {mode}<br>'
            "Volunteer selection: {volunteer_mode}<br>"
            "Ambulance repositioning: {ambulance_reposition}</p>"
        ).format(
            path=STOP_PATH,
            mode=html.escape(
                scenarios.ambulance_mode_title(scenarios.current_ambulance_mode)
            ),
            volunteer_mode=html.escape(
                scenarios.volunteer_mode_title(scenarios.current_volunteer_mode)
            ),
            ambulance_reposition=html.escape(
                scenarios.ambulance_reposition_title(
                    scenarios.current_ambulance_reposition
                )
            ),
        )

    buttons = "".join(
        '<button class="scenario{active}" type="submit" name="scenario" value="{name}">'
        "{title}</button>".format(
            active=" selected" if name == selected else "",
            name=html.escape(name, quote=True),
            title=html.escape(scenarios.scenario_title(name)),
        )
        for name in scenarios.scenario_names()
    )
    controls = (
        '<form class="scenarios" method="get" action="{path}">'
        '<input type="hidden" name="ambulance_mode" value="{ambulance_mode}">'
        '<input type="hidden" name="volunteer_mode" value="{volunteer_mode}">'
        '<input type="hidden" name="ambulance_reposition" value="{reposition}">'
        "{buttons}</form>"
    ).format(
        path=ENVIRONMENT_PATH,
        ambulance_mode=html.escape(ambulance_mode, quote=True),
        volunteer_mode=html.escape(volunteer_mode, quote=True),
        reposition=html.escape(ambulance_reposition, quote=True),
        buttons=buttons,
    )

    volunteer_toggle = _volunteer_mode_button(
        selected, volunteer_mode, ambulance_mode, ambulance_reposition
    )
    reposition_toggle = _ambulance_reposition_button(
        selected, ambulance_mode, volunteer_mode, ambulance_reposition
    )
    toggles = volunteer_toggle + reposition_toggle

    if selected is None:
        return controls + toggles + (
            '<p class="hint">Choose a scenario to preview where its agents start.</p>'
        )

    return controls + toggles + (
        '<form class="start-form" method="post" action="{action}">'
        '<input type="hidden" name="scenario" value="{name}">'
        '<input type="hidden" name="volunteer_mode" value="{volunteer_mode}">'
        '<input type="hidden" name="ambulance_reposition" value="{reposition}">'
        "{mode}"
        '<button class="start" type="submit">Start selected scenario</button>'
        "</form>"
        '<a class="clear" href="{environment}">Close preview</a>'
    ).format(
        action=START_PATH,
        name=html.escape(selected, quote=True),
        volunteer_mode=html.escape(volunteer_mode, quote=True),
        reposition=html.escape(ambulance_reposition, quote=True),
        mode=_ambulance_mode_select(ambulance_mode),
        environment=ENVIRONMENT_PATH,
    )


def _format_metric(value, count):
    """Format a duration for the metrics table (seconds, two decimals)."""
    return f"{value:.2f}s" if count else "—"


def _metrics_html():
    """Return the metrics table shown while/after a simulation runs."""
    summary = agents.Metrics.summary()
    rows = (
        ("Collapse to aid (volunteer or ambulance)", summary["aid"]),
        ("Collapse to hospital arrival", summary["hospital"]),
    )
    body = "".join(
        "<tr><th>{label}</th><td>{count}</td><td>{total}</td>"
        "<td>{mean}</td><td>{maximum}</td></tr>".format(
            label=html.escape(label),
            count=stats["count"],
            total=_format_metric(stats["sum"], stats["count"]),
            mean=_format_metric(stats["mean"], stats["count"]),
            maximum=_format_metric(stats["max"], stats["count"]),
        )
        for label, stats in rows
    )
    return (
        '<section class="metrics">'
        "<h2>Metrics</h2>"
        "<table><thead><tr>"
        "<th>Measure</th><th>Cases</th><th>Sum</th><th>Mean</th><th>Max</th>"
        "</tr></thead><tbody>{body}</tbody></table>"
        '<p class="hint">Times are in seconds from the collapse. False alarms '
        "count for aid, but not for hospital arrival.</p>"
        "</section>"
    ).format(body=body)


def render_environment_html(preview=None, ambulance_mode=None, volunteer_mode=None,
                            ambulance_reposition=None):
    """Build the HTML page showing the live environment or a scenario preview."""
    width, height = agents.Environment_width, agents.Environment_height
    if ambulance_mode not in agents.AMBULANCE_MODES:
        ambulance_mode = scenarios.current_ambulance_mode
    if volunteer_mode not in agents.VOLUNTEER_MODES:
        volunteer_mode = scenarios.current_volunteer_mode
    if ambulance_reposition not in agents.AMBULANCE_REPOSITION_MODES:
        ambulance_reposition = scenarios.current_ambulance_reposition

    if scenarios.simulation_running:
        preview = None
        scenario = scenarios.current_scenario or "default"
        mode_title = scenarios.ambulance_mode_title(scenarios.current_ambulance_mode)
        status = f"Simulation running ({scenario}) - {mode_title}"
        status_class = "running"
        layout = [
            [_describe_cell(agents.Environment[y][x]) for x in range(width)]
            for y in range(height)
        ]
        people, volunteers, ambulances, hospitals = _live_counts()
    elif preview is not None:
        status = f"Preview: {scenarios.scenario_title(preview)}"
        status_class = "preview"
        layout = _preview_layout(preview)
        people, volunteers, ambulances, hospitals = _preview_counts(preview)
    else:
        status, status_class = "Not started", "stopped"
        layout = [
            [_describe_cell(agents.Environment[y][x]) for x in range(width)]
            for y in range(height)
        ]
        people, volunteers, ambulances, hospitals = _live_counts()

    counts = (
        "people: {people} / volunteers: {volunteers} / "
        "ambulances: {ambulances} / hospitals: {hospitals}"
    ).format(
        people=people, volunteers=volunteers, ambulances=ambulances, hospitals=hospitals
    )

    show_metrics = scenarios.simulation_running or (
        preview is None and agents.Metrics.has_data()
    )
    metrics_html = _metrics_html() if show_metrics else ""

    cells = []
    for y in range(height):
        for x in range(width):
            label, css_class, tooltip = layout[y][x]
            title = f"({x},{y}) {tooltip}".strip()
            cells.append(
                '<div class="cell {cls}" title="{title}" '
                'style="grid-row:{row};grid-column:{col};">{label}</div>'.format(
                    cls=css_class,
                    title=html.escape(title, quote=True),
                    row=y + 1,
                    col=x + 1,
                    label=html.escape(label),
                )
            )

    refresh_meta = (
        f'<meta http-equiv="refresh" content="{REFRESH_INTERVAL}">'
        if scenarios.simulation_running else ""
    )

    page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{refresh_meta}
<title>Environment - SPADE</title>
<style>
  body {{ margin: 24px; background: #12141a; color: #e6e6e6;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .meta {{ color: #9aa0aa; margin: 0 0 16px; font-size: 13px; }}
  .hint {{ color: #9aa0aa; margin: 0 0 16px; font-size: 13px; }}
  .grid {{ display: grid; gap: 3px;
           grid-template-columns: repeat({width}, 36px);
           grid-template-rows: repeat({height}, 36px); }}
  .cell {{ display: flex; align-items: center; justify-content: center;
           border-radius: 5px; background: #1e2129;
           font-family: ui-monospace, "SF Mono", Consolas, monospace;
           font-size: 13px; font-weight: 600; }}
  .hospital {{ background: #1f8a4c; }}
  .ambulance {{ background: #b8392f; }}
  .collapsed {{ background: #d59a1a; color: #141414; }}
  .person {{ background: #2f6fd0; }}
  .volunteer {{ background: #7b5cd6; }}
  .other {{ background: #4a4f5a; }}
  .legend {{ display: flex; gap: 16px; flex-wrap: wrap;
             margin: 18px 0 0; padding: 0; list-style: none;
             font-size: 13px; color: #b9bec7; }}
  .legend span {{ display: inline-block; width: 12px; height: 12px;
                  border-radius: 3px; margin-right: 6px;
                  vertical-align: -1px; }}
  .metrics {{ margin: 20px 0 0; }}
  .metrics h2 {{ font-size: 15px; margin: 0 0 8px; }}
  .metrics table {{ border-collapse: collapse; font-size: 13px; }}
  .metrics th, .metrics td {{ padding: 6px 12px; text-align: left;
                              border-bottom: 1px solid #2a2e37; }}
  .metrics thead th {{ color: #9aa0aa; font-weight: 600; }}
  .metrics tbody th {{ color: #cfd4dc; font-weight: 600; }}
  .metrics td {{ color: #b9bec7; font-variant-numeric: tabular-nums; }}
  .status {{ display: inline-block; padding: 3px 10px; border-radius: 999px;
             font-size: 12px; font-weight: 600; margin-left: 8px; }}
  .running {{ background: #1f8a4c; color: #fff; }}
  .preview {{ background: #2f6fd0; color: #fff; }}
  .stopped {{ background: #3a3f4a; color: #cfd4dc; }}
  .scenarios {{ display: block; margin: 0 0 4px; }}
  .scenario {{ display: inline-block; margin: 0 8px 8px 0; padding: 8px 14px;
               border: 1px solid #3a3f4a; border-radius: 6px; background: #1e2129;
               color: #cfd4dc; font-size: 13px; font-weight: 600;
               cursor: pointer; }}
  .scenario:hover {{ background: #262a34; }}
  .scenario.selected {{ border-color: #2f6fd0; background: #1b2c46; color: #eaf1ff; }}
  .mode {{ display: inline-block; margin: 0 12px 16px 0; font-size: 13px;
           color: #b9bec7; }}
  .mode select {{ margin-left: 6px; padding: 8px 10px; border: 1px solid #3a3f4a;
                  border-radius: 6px; background: #1e2129; color: #cfd4dc;
                  font-size: 13px; }}
  .start, .stop {{ display: inline-block; margin: 0 8px 16px 0; padding: 9px 18px;
                   border: 0; border-radius: 6px; color: #fff;
                   font-size: 14px; font-weight: 600; cursor: pointer; }}
  .start {{ background: #2f6fd0; }}
  .start:hover {{ background: #3d7fe0; }}
  .stop {{ background: #b8392f; }}
  .stop:hover {{ background: #cf4636; }}
  .clear {{ margin-left: 12px; font-size: 13px; }}
  form {{ display: inline; }}
  a {{ color: #6fb0ff; }}
</style>
</head>
<body>
<h1>Environment <span class="status {status_class}">{status}</span></h1>
<p class="meta">{width} x {height} grid &middot; {counts}</p>
{controls}
{metrics}
<div class="grid">
{cells}
</div>
<ul class="legend">
  <li><span class="hospital"></span>hospital</li>
  <li><span class="ambulance"></span>ambulance</li>
  <li><span class="person"></span>person</li>
  <li><span class="volunteer"></span>volunteer</li>
  <li><span class="collapsed"></span>collapsed</li>
</ul>
<p class="meta"><a href="/spade">Back to the SPADE interface</a></p>
</body>
</html>
""".format(
        refresh_meta=refresh_meta,
        width=width,
        height=height,
        counts=html.escape(counts),
        status=html.escape(status),
        status_class=status_class,
        controls=_scenario_controls(
            preview, ambulance_mode, volunteer_mode, ambulance_reposition
        ),
        metrics=metrics_html,
        cells="\n".join(cells),
    )
    return page


class WebOperator(agents.Operator):
    """The operator agent from agents.py, with the environment page added to its web UI."""

    async def setup(self):
        # Run the base operator setup first: it creates the operator state
        # (incidents, offers and ambulance assignments) and starts
        # Operator_Behav.
        await super().setup()

        self.web.add_get(
            ENVIRONMENT_PATH, self.environment_page, template=None, raw=True
        )
        self.web.add_post(
            START_PATH, self.start_simulation_page, template=None, raw=True
        )
        self.web.add_post(
            STOP_PATH, self.stop_simulation_page, template=None, raw=True
        )
        self.web.add_menu_entry(
            "Environment", ENVIRONMENT_PATH, icon="fa fa-map"
        )

        port = WEB_PORT
        if not _port_is_free(WEB_HOST, port):
            port = unused_port(WEB_HOST)
            logger.warning(
                "Port %s is busy, serving the web interface on %s instead.",
                WEB_PORT,
                port,
            )
        self.web.start(hostname=WEB_HOST, port=port)
        logger.info("Environment page: http://%s:%s%s", WEB_HOST, port, ENVIRONMENT_PATH)

    async def environment_page(self, request):
        preview = request.query.get("scenario")
        if preview not in scenarios.SCENARIOS:
            preview = None
        ambulance_mode = request.query.get("ambulance_mode")
        if ambulance_mode not in agents.AMBULANCE_MODES:
            ambulance_mode = scenarios.current_ambulance_mode
        volunteer_mode = request.query.get("volunteer_mode")
        if volunteer_mode not in agents.VOLUNTEER_MODES:
            volunteer_mode = scenarios.current_volunteer_mode
        ambulance_reposition = request.query.get("ambulance_reposition")
        if ambulance_reposition not in agents.AMBULANCE_REPOSITION_MODES:
            ambulance_reposition = scenarios.current_ambulance_reposition
        return aioweb.Response(
            text=render_environment_html(
                preview, ambulance_mode, volunteer_mode, ambulance_reposition
            ),
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def start_simulation_page(self, request):
        data = await request.post()
        name = data.get("scenario", "default")
        if name not in scenarios.SCENARIOS:
            name = "default"
        ambulance_mode = data.get("ambulance_mode", agents.AMBULANCE_MODE_CLOSEST)
        if ambulance_mode not in agents.AMBULANCE_MODES:
            ambulance_mode = agents.AMBULANCE_MODE_CLOSEST
        volunteer_mode = data.get("volunteer_mode", agents.VOLUNTEER_MODE_BIDDING)
        if volunteer_mode not in agents.VOLUNTEER_MODES:
            volunteer_mode = agents.VOLUNTEER_MODE_BIDDING
        ambulance_reposition = data.get(
            "ambulance_reposition", agents.AMBULANCE_REPOSITION_OFF
        )
        if ambulance_reposition not in agents.AMBULANCE_REPOSITION_MODES:
            ambulance_reposition = agents.AMBULANCE_REPOSITION_OFF
        if scenarios.start_simulation(
            name, ambulance_mode=ambulance_mode, volunteer_mode=volunteer_mode,
            ambulance_reposition=ambulance_reposition,
        ):
            logger.info(
                "%s started from the web interface "
                "(ambulance selection: %s; volunteer selection: %s; "
                "ambulance repositioning: %s).",
                scenarios.scenario_title(name),
                scenarios.ambulance_mode_title(ambulance_mode),
                scenarios.volunteer_mode_title(volunteer_mode),
                scenarios.ambulance_reposition_title(ambulance_reposition),
            )
        raise aioweb.HTTPFound(ENVIRONMENT_PATH)

    async def stop_simulation_page(self, request):
        if await scenarios.stop_simulation():
            logger.info("Simulation stopped from the web interface.")
        raise aioweb.HTTPFound(ENVIRONMENT_PATH)


async def run():
    """Start the operator (and its web UI) and keep the process alive.

    The scenarios are started later, from the buttons on the environment page.
    """
    await scenarios.create_operator(WebOperator)
    await asyncio.Event().wait()


def main():
    spade.run(run(),embedded_xmpp_server=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    main()
