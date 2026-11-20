"""Scenarios that populate the environment with agents and drive the simulation.

Each scenario is described by a list of ``AgentPlacement`` objects, so the web
interface can preview where the agents will start without creating them. The
same list is used to build the agents when the scenario is started.

A scenario also carries its own list of ``ScenarioEvent`` objects. The
simulation loop only runs the events of the selected scenario, each one at its
own moment (seconds after the scenario is ready), so different scenarios can
trigger different things at different times.
"""

import asyncio
from dataclasses import dataclass
from typing import Callable

import spade

from agents import (
    AMBULANCE_MODE_CLOSEST,
    AMBULANCE_MODES,
    Ambulances,
    EmergencyAmbulance,
    Environment,
    Hospital,
    Hospital_coords,
    Metrics,
    Operator,
    VolunteerAgent,
    Volunteers,
    WalkingAgent,
)


@dataclass(frozen=True)
class AgentPlacement:
    """Where one agent starts and the first place it walks to."""

    kind: str                       # "person", "volunteer" or "ambulance"
    jid: str
    x: int
    y: int
    target: tuple = None
    walk_speed: float = None


@dataclass(frozen=True)
class ScenarioEvent:
    """Something that happens a number of seconds into a scenario."""

    at: float
    action: Callable        # async def action(spawned: dict[str, Agent]) -> None
    description: str = ""


def collapse_at(seconds, jid, false_alarm=False):
    """Return an event that makes the given agent collapse at ``seconds``.

    Set ``false_alarm=True`` for a collapse that only needs a volunteer (the
    ambulance is stood down once a volunteer or an ambulance identifies it).
    """
    async def action(spawned):
        agent = spawned.get(jid)
        if agent is not None:
            await agent.collapse(false_alarm=false_alarm)

    return ScenarioEvent(seconds, action, f"{jid} collapses")


DEFAULT_PLACEMENTS = (
    AgentPlacement("person", "agent0@localhost", 0, 0,
                   target=(16, 8), walk_speed=0.5),
    AgentPlacement("person", "agent1@localhost", 10, 0,
                   target=(0, 8), walk_speed=1),
)

AMBULANCE_PLACEMENT0 = (
    AgentPlacement("ambulance", "ambulance0@localhost", 19, 9),
    AgentPlacement("ambulance", "ambulance1@localhost", 24, 2),
)

AMBULANCE_PLACEMENT1 = (
    AgentPlacement("ambulance", "ambulance0@localhost", 24, 14),
)

VOLUNTEER_PLACEMENTS0 = (
    AgentPlacement("volunteer", "volunteer0@localhost", 2, 3),
    AgentPlacement("volunteer", "volunteer1@localhost", 16, 6),
)

MULTIPLE_COLLAPSE_PLACEMENT = (
    AgentPlacement("person", "agent0@localhost", 5, 5,
                   walk_speed=2),
    AgentPlacement("person", "agent1@localhost", 0, 4,
                   walk_speed=1),
    AgentPlacement("person", "agent2@localhost", 0, 2,
                   walk_speed=2),
    AgentPlacement("person", "agent3@localhost", 6, 5,
                   walk_speed=1),
    AgentPlacement("ambulance", "ambulance0@localhost", 22, 14),
    AgentPlacement("ambulance", "ambulance1@localhost", 15, 5),
)

MULTIPLE_COLLAPSE_VOLUNTEER = (
    AgentPlacement("volunteer", "volunteer0@localhost", 0, 8),
)

HOSPITAL_POSITION = (8, 8)

SCENARIOS = {
    "default": {
        "title": "Default scenario",
        "placements": DEFAULT_PLACEMENTS + AMBULANCE_PLACEMENT0,
        "events": (
            collapse_at(5, "agent0@localhost"),
        ),
    },
    "scenario1": {
        "title": "Scenario 1 (with volunteers)",
        "placements": VOLUNTEER_PLACEMENTS0 + DEFAULT_PLACEMENTS + AMBULANCE_PLACEMENT0,
        "events": (
            collapse_at(5, "agent0@localhost"),
        ),
    },
    "falseAlarmExample": {
        "title": "False alarm example (with volunteers)",
        "placements": VOLUNTEER_PLACEMENTS0 + DEFAULT_PLACEMENTS + AMBULANCE_PLACEMENT1,
        "events": (
            collapse_at(5, "agent0@localhost", false_alarm=True),
        ),
    },
    "MultipleCollapse": {
        "title": "Multiple Collapse Example",
        "placements": MULTIPLE_COLLAPSE_PLACEMENT,
        "events": (
            collapse_at(0, "agent0@localhost", false_alarm=False),
            collapse_at(0, "agent1@localhost", false_alarm=False)
        ),
    },
    "MultipleCollapseFalseAlarm": {
        "title": "Multiple Collapse with False Alarm",
        "placements": MULTIPLE_COLLAPSE_PLACEMENT,
        "events": (
            collapse_at(0, "agent0@localhost", false_alarm=True),
            collapse_at(0, "agent1@localhost", false_alarm=False)
        ),
    },
}

simulation_running = False
simulation_agents = []
simulation_task = None
current_scenario = None
current_ambulance_mode = AMBULANCE_MODE_CLOSEST
operator = None


def ambulance_mode_names():
    """Return the available ambulance selection modes."""
    return list(AMBULANCE_MODES)


def ambulance_mode_title(mode):
    """Return the human readable name of an ambulance selection mode."""
    return AMBULANCE_MODES.get(mode, AMBULANCE_MODES[AMBULANCE_MODE_CLOSEST])


def scenario_names():
    """Return the scenario identifiers in the order shown in the web UI."""
    return list(SCENARIOS)


def scenario_title(name):
    """Return the human readable name of a scenario."""
    return SCENARIOS[name]["title"]


def scenario_placements(name):
    """Return the starting placements of a scenario (used by the web preview)."""
    return SCENARIOS[name]["placements"]


async def create_operator(operator_class=Operator):
    """Create and start the operator agent (it also hosts the web UI)."""
    global operator
    if operator is not None:
        return operator
    operator = operator_class("operator@localhost", "password")
    await operator.start(auto_register=True)
    return operator


def reset_environment():
    """Remove every agent and hospital from the grid."""
    for row in Environment:
        for cell in row:
            cell.clear()
    Hospital_coords.clear()
    Ambulances.clear()
    Volunteers.clear()


def _create_agent(placement):
    """Build the agent described by a placement."""
    if placement.kind == "person":
        return WalkingAgent(placement.jid, "password")
    if placement.kind == "volunteer":
        return VolunteerAgent(
            placement.jid, "password", status="AVAILABLE",
            training="basic", has_firstAid=True,
        )
    if placement.kind == "ambulance":
        return EmergencyAmbulance(placement.jid, "password")
    raise ValueError(f"Unknown agent kind: {placement.kind}")


async def setup_scenario(name="default"):
    """Create and place the agents of one scenario, then start them."""
    global simulation_agents
    scenario = SCENARIOS[name]
    Metrics.reset()
    simulation_agents = []
    spawned = {}

    for placement in scenario["placements"]:
        agent = _create_agent(placement)
        simulation_agents.append(agent)
        spawned[placement.jid] = agent
        await agent.insert(placement.x, placement.y)
        if placement.target is not None:
            await agent.move_to(*placement.target, placement.walk_speed)
        await agent.start(auto_register=True)

    Hospital(*HOSPITAL_POSITION)
    return spawned


def print_environment():
    """Print the current environment grid to the console (used standalone)."""
    for row in Environment:
        line = ""
        for cell in row:
            if not cell:
                line += "."
            elif any(isinstance(obj, Hospital) for obj in cell):
                line += "H"
            elif any(isinstance(obj, EmergencyAmbulance) for obj in cell):
                line += "A"
            elif any(isinstance(obj, VolunteerAgent) for obj in cell):
                line += "V"
            else:
                line += str(len(cell))
        print(line)
    print()


async def simulation_loop(events=(), spawned=None):
    """Advance the scenario, running its own events at their moments.

    Event times are counted in seconds from the moment the scenario is ready
    (i.e. after all of its agents have been started).
    """
    global simulation_running
    spawned = spawned or {}
    started = asyncio.get_running_loop().time()
    pending = sorted(events, key=lambda event: event.at)

    while simulation_running:
        print_environment()
        now = asyncio.get_running_loop().time()
        while pending and now - started >= pending[0].at:
            event = pending.pop(0)
            if event.description:
                print(f"[scenario] {event.description}")
            await event.action(spawned)
        await asyncio.sleep(1)


async def run_simulation(name):
    """Run one scenario until it is stopped or the loop is cancelled."""
    global simulation_running
    if operator is not None:
        operator.ambulance_mode = current_ambulance_mode
    try:
        spawned = await setup_scenario(name)
        await simulation_loop(SCENARIOS[name]["events"], spawned)
    finally:
        simulation_running = False


def start_simulation(name="default", ambulance_mode=AMBULANCE_MODE_CLOSEST):
    """Start a scenario. Returns False if one is already running."""
    global simulation_running, simulation_task, current_scenario
    global current_ambulance_mode
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {name}")
    if simulation_running:
        return False
    if ambulance_mode not in AMBULANCE_MODES:
        ambulance_mode = AMBULANCE_MODE_CLOSEST
    simulation_running = True
    current_scenario = name
    current_ambulance_mode = ambulance_mode
    Metrics.reset()
    if operator is not None:
        operator.ambulance_mode = ambulance_mode
    simulation_task = asyncio.create_task(run_simulation(name))
    return True


async def stop_simulation():
    """Stop the running scenario, shut its agents down and clear the grid."""
    global simulation_running, simulation_agents, simulation_task
    global current_scenario, current_ambulance_mode
    if not simulation_running and not simulation_agents:
        return False

    simulation_running = False
    task, simulation_task = simulation_task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for agent in list(simulation_agents):
        await agent.stop()
    simulation_agents = []

    reset_environment()
    current_ambulance_mode = AMBULANCE_MODE_CLOSEST
    if operator is not None:
        operator.reported_incidents.clear()
        operator.pending_offers.clear()
        operator.dispatched_ambulances.clear()
        operator.busy_ambulances.clear()
        operator.handled_incidents.clear()
        operator.ambulance_mode = AMBULANCE_MODE_CLOSEST

    current_scenario = None
    return True


async def main(start_scenario=True):
    """Run the operator and (optionally) the default scenario, forever."""
    await create_operator()
    if start_scenario:
        start_simulation()
    await asyncio.Event().wait()


if __name__ == "__main__":
    spade.run(main())
