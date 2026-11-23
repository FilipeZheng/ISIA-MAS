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
from dataclasses import dataclass, replace
from typing import Callable

import spade

from agents import (
    AEDAgent,
    AMBULANCE_MODE_CLOSEST,
    AMBULANCE_MODES,
    AMBULANCE_REPOSITION_CORNERS,
    AMBULANCE_REPOSITION_MODES,
    AMBULANCE_REPOSITION_OFF,
    AMBULANCE_REPOSITION_ON,
    VOLUNTEER_MODE_BIDDING,
    VOLUNTEER_MODES,
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

    kind: str                       # "person", "aed" or "ambulance"
    jid: str
    x: int
    y: int
    target: tuple = None
    walk_speed: float = None
    has_aed: bool = False           # only used by ambulances
    has_citizen_sensor: bool = False  # whether the agent carries a
                                     # citizen sensor


@dataclass(frozen=True)
class ScenarioEvent:
    """Something that happens a number of seconds into a scenario."""

    at: float
    action: Callable        # async def action(spawned: dict[str, Agent]) -> None
    description: str = ""


def collapse_at(seconds, jid, false_alarm=False, severity=1):
    """Return an event that makes the given agent collapse at ``seconds``.

    Set ``false_alarm=True`` for a collapse that only needs a responder (the
    ambulance is stood down once a responder or an ambulance identifies it).
    Set ``severity=3`` for a collapse that needs an AED (unless the responding
    ambulance carries one).
    """
    async def action(spawned):
        agent = spawned.get(jid)
        if agent is not None:
            await agent.collapse(false_alarm=false_alarm, severity=severity)

    return ScenarioEvent(seconds, action, f"{jid} collapses")


DEFAULT_PLACEMENTS = (
    AgentPlacement("person", "agent0@localhost", 0, 0,
                   target=(16, 8), walk_speed=0.5),
    AgentPlacement("person", "agent1@localhost", 10, 0,
                   target=(0, 8), walk_speed=1),
)

# Like DEFAULT_PLACEMENTS, but the people carry a citizen sensor.
ALL_COLLAPSE_CIT_SENSOR_PLACEMENTS = tuple(
    replace(placement, has_citizen_sensor=True)
    for placement in DEFAULT_PLACEMENTS
)

AMBULANCE_PLACEMENT0 = (
    AgentPlacement("ambulance", "ambulance0@localhost", 15, 9),
    AgentPlacement("ambulance", "ambulance1@localhost", 24, 2),
)

AMBULANCE_PLACEMENT1 = (
    AgentPlacement("ambulance", "ambulance0@localhost", 24, 14),
)

AED_PLACEMENTS0 = (
    AgentPlacement("aed", "aed0@localhost", 2, 3),
    AgentPlacement("aed", "aed1@localhost", 16, 6),
)

# Test scenario for volunteer BDI reasoning
VOLUNTEER_BDI_PLACEMENTS = (
    AgentPlacement("person", "patient_bdi@localhost", 8, 6),
    AgentPlacement("person", "bystander_bdi@localhost", 8, 2),
    AgentPlacement("volunteer", "volunteer0@localhost", 5, 6),
    AgentPlacement("volunteer", "volunteer1@localhost", 16, 6),
    AgentPlacement("ambulance", "ambulance_bdi@localhost", 24, 14),
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

MULTIPLE_COLLAPSE_AED = (
    AgentPlacement("aed", "aed0@localhost", 0, 8),
)

# Two collapses that the ambulance-selection mode has to handle differently:
# a severity-3 collapse (needs an AED) and a severity-1 collapse, with one
# ambulance nearby (no AED) and one far away (carries an AED). The second
# victim is an AED agent, which may also be drawn towards the first collapse
# as a bystander before it goes down itself.
DISPATCH_MODE_PLACEMENT = (
    AgentPlacement("person", "agent0@localhost", 3, 5),
    AgentPlacement("person", "bystander0@localhost", 3, 3),
    AgentPlacement("aed", "agent1@localhost", 3, 6),
    AgentPlacement("ambulance", "ambulance0@localhost", 0, 6),
    AgentPlacement("ambulance", "ambulance1@localhost", 24, 14,
                   has_aed=True),
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
    "AllCollapseWithCitSensors": {
        "title": "All collapse, everyone with a citizen sensor",
        "placements": ALL_COLLAPSE_CIT_SENSOR_PLACEMENTS + AMBULANCE_PLACEMENT0,
        "events": (
            collapse_at(5, "agent0@localhost"),
            collapse_at(5, "agent1@localhost"),
        ),
    },
    "AED_example": {
        "title": "AED Example",
        "placements": AED_PLACEMENTS0 + DEFAULT_PLACEMENTS + AMBULANCE_PLACEMENT0,
        "events": (
            collapse_at(2, "agent0@localhost",severity=3),
        ),
    },
    "falseAlarmExample": {
        "title": "False alarm example (with AED agents)",
        "placements": AED_PLACEMENTS0 + DEFAULT_PLACEMENTS + AMBULANCE_PLACEMENT1,
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
    
    "volunteerBDI": {
        "title": "Volunteer BDI Response (two volunteers)",
        "placements": VOLUNTEER_BDI_PLACEMENTS,
        "events": (
            collapse_at(5, "patient_bdi@localhost"),
        ),
    },
    "dispatchModes": {
        "title": "Ambulance dispatch modes (closest / reassign / bidding)",
        "placements": DISPATCH_MODE_PLACEMENT,
        "events": (
            collapse_at(5, "agent0@localhost", severity=3),
            collapse_at(7, "agent1@localhost", severity=1),
        ),
    },
}

simulation_running = False
simulation_agents = []
simulation_task = None
current_scenario = None
current_ambulance_mode = AMBULANCE_MODE_CLOSEST
current_volunteer_mode = VOLUNTEER_MODE_BIDDING
current_ambulance_reposition = AMBULANCE_REPOSITION_OFF
operator = None


def ambulance_mode_names():
    """Return the available ambulance selection modes."""
    return list(AMBULANCE_MODES)


def ambulance_mode_title(mode):
    """Return the human readable name of an ambulance selection mode."""
    return AMBULANCE_MODES.get(mode, AMBULANCE_MODES[AMBULANCE_MODE_CLOSEST])


def volunteer_mode_names():
    """Return the available volunteer selection modes."""
    return list(VOLUNTEER_MODES)


def volunteer_mode_title(mode):
    """Return the human readable name of a volunteer selection mode."""
    return VOLUNTEER_MODES.get(mode, VOLUNTEER_MODES[VOLUNTEER_MODE_BIDDING])


def ambulance_reposition_title(mode):
    """Return the human readable name of an ambulance reposition mode."""
    return AMBULANCE_REPOSITION_MODES.get(
        mode, AMBULANCE_REPOSITION_MODES[AMBULANCE_REPOSITION_OFF]
    )


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
        agent = WalkingAgent(placement.jid, "password")
    elif placement.kind == "aed":
        agent = AEDAgent(placement.jid, "password")
    elif placement.kind == "volunteer":
        agent = VolunteerAgent(
            placement.jid, "password",
            status="AVAILABLE",
            training="basic",
            has_firstAid=True,
            bidding=current_volunteer_mode == VOLUNTEER_MODE_BIDDING,
        )
    elif placement.kind == "ambulance":
        agent = EmergencyAmbulance(
            placement.jid, "password",
            has_aed=placement.has_aed,
            reposition=(
                current_ambulance_reposition
                in (AMBULANCE_REPOSITION_ON, AMBULANCE_REPOSITION_CORNERS)
            ),
        )
    else:
        raise ValueError(f"Unknown agent kind: {placement.kind}")
    agent.has_citizen_sensor = placement.has_citizen_sensor
    return agent


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

    # Start the agents together: the XMPP connection of each agent is the
    # slow part and it is independent for every agent, so waiting for the
    # agents one by one makes loading a scenario needlessly slow.
    await asyncio.gather(
        *(agent.start(auto_register=True) for agent in simulation_agents)
    )

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
            elif any(isinstance(obj, AEDAgent) for obj in cell):
                line += "D"
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
        operator.volunteer_mode = current_volunteer_mode
        operator.ambulance_reposition = current_ambulance_reposition
    try:
        spawned = await setup_scenario(name)
        await simulation_loop(SCENARIOS[name]["events"], spawned)
    finally:
        simulation_running = False


def start_simulation(name="default", ambulance_mode=AMBULANCE_MODE_CLOSEST,
                     volunteer_mode=VOLUNTEER_MODE_BIDDING,
                     ambulance_reposition=AMBULANCE_REPOSITION_OFF):
    """Start a scenario. Returns False if one is already running."""
    global simulation_running, simulation_task, current_scenario
    global current_ambulance_mode, current_volunteer_mode
    global current_ambulance_reposition
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {name}")
    if simulation_running:
        return False
    if ambulance_mode not in AMBULANCE_MODES:
        ambulance_mode = AMBULANCE_MODE_CLOSEST
    if volunteer_mode not in VOLUNTEER_MODES:
        volunteer_mode = VOLUNTEER_MODE_BIDDING
    if ambulance_reposition not in AMBULANCE_REPOSITION_MODES:
        ambulance_reposition = AMBULANCE_REPOSITION_OFF
    simulation_running = True
    current_scenario = name
    current_ambulance_mode = ambulance_mode
    current_volunteer_mode = volunteer_mode
    current_ambulance_reposition = ambulance_reposition
    Metrics.reset()
    if operator is not None:
        operator.ambulance_mode = ambulance_mode
        operator.volunteer_mode = volunteer_mode
        operator.ambulance_reposition = ambulance_reposition
    simulation_task = asyncio.create_task(run_simulation(name))
    return True


async def stop_simulation():
    """Stop the running scenario, shut its agents down and clear the grid."""
    global simulation_running, simulation_agents, simulation_task
    global current_scenario, current_ambulance_mode, current_volunteer_mode
    global current_ambulance_reposition
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
    current_volunteer_mode = VOLUNTEER_MODE_BIDDING
    current_ambulance_reposition = AMBULANCE_REPOSITION_OFF
    if operator is not None:
        operator.reported_incidents.clear()
        operator.pending_offers.clear()
        operator.pending_ambulance_offers.clear()
        operator.dispatched_ambulances.clear()
        operator.busy_ambulances.clear()
        operator.handled_incidents.clear()
        operator.ambulance_mode = AMBULANCE_MODE_CLOSEST
        operator.volunteer_mode = VOLUNTEER_MODE_BIDDING
        operator.ambulance_reposition = AMBULANCE_REPOSITION_OFF

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
