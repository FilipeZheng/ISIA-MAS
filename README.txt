Decentralised Community Emergency Medical Response


A SPADE-based multi-agent simulation of an urban emergency response
scenario, with people, volunteers, ambulances and AED agents on a grid.

RUNNING


spade_venv/bin/python main.py          (Linux/macOS)
spade_venv\Scripts\python.exe main.py  (Windows)

If spade is not importable from the current interpreter, main.py
re-executes itself with the interpreter from spade_venv automatically.

The operator agent starts an server and serves the
environment page at http://127.0.0.1:10000/spade/environment/
(SPADE_WEB_PORT overrides the port). While a simulation is running the
page refreshes itself every second.

USING THE WEB UI
----------------

- Scenario buttons: preview the starting positions of every scenario
  (see the list below).
- Ambulance selection (how the operator chooses which ambulance
  responds to a collapse):
  - "Closest free ambulance (default)": the closest free ambulance.
  - "Reassign if a closer ambulance becomes free": 
    when an ambulance becomes free and a closer one is free, the response is switched to it.
  - "Bidding (ambulances offer, best value wins)": 
    every free ambulance offers a value; the best offer wins (closer and AED-equipped ambulances are rewarded).
  - "Reassign + bidding (bidding on collapse; redo bidding when an
    ambulance becomes available)": bidding on collapse, plus: when an
    ambulance becomes available, outstanding incidents get a new bidding
    round, and an incident's assignment is switched to a clearly closer
    free option when one exists.
- Volunteer selection: bidding (volunteers offer, closest wins) or
  closest available volunteer (no bidding).
- Ambulance repositioning: what a free ambulance does after going idle:
  "Off" (stay where it is), "On" (move to the spot furthest from other ambulances)
  or "Corners" (move to the spot furthest from other ambulances AND the four grid corners).
- Start selected scenario / stop running simulation buttons.
- Metrics table: collapse -> aid (volunteer or ambulance) and
  collapse -> hospital arrival, in seconds from the collapse.

SCENARIOS
---------

default - "Default scenario" - 2 people + 2 ambulances; the first person
  collapses at t=5s.

AllCollapseWithCitSensors - "All collapse, everyone with a citizen
  sensor" - like default, but everyone collapses and each person carries
  a citizen sensor that notifies the operator of its collapse.

AED_example - "AED Example" - a severity-3 collapse (needs an AED).

falseAlarmExample - "False alarm example (with AED agents)" - a collapse
  that is a false alarm.

MultipleCollapse - "Multiple Collapse Example" - several people
  collapse at t=0.

MultipleCollapseFalseAlarm - "Multiple Collapse with False Alarm".


Programmatically: scenarios.SCENARIOS[name]["title"] and
scenarios.scenario_names().

AGENTS AND BEHAVIOURS
---------------------

People (WalkingAgent) - walk to their target when not collapsed; 
  when they collapse, nearby people go to them and report the collapse to the operator; 
  a person can carry a citizen sensor (has_citizen_sensor) which tells the operator when it collapses.

Volunteers (VolunteerAgent, BDI) - offer help (bidding or direct), 
  get assigned, respond to collapses and confirm them to the operator.

Ambulances (EmergencyAmbulance) - respond to collapses 
  (FSM states idling -> moving -> helping -> in-hospital); 
  can carry an AED; when free they can reposition; report when back in service.

AED agents (AEDAgent) - carry AEDs and move them to severity-3 collapses.

Operator - dispatches ambulances and volunteers, 
  replies to bystander/citizen-sensor collapse reports, 
  stands down ambulances for false alarms, tracks metrics.

The environment grid is shared: every agent registers its
position (agents.Environment) so all agents can see the others.

MODULES
-------

agents.py - agents, behaviours and the shared environment grid.
scenarios.py - scenario definitions (agent placements + events) and the
  simulation loop (start_simulation, stop_simulation, setup_scenario,
  ...).
main.py - runs the operator + web UI and hosts the SPADE environment
  page.
volunteer.asl - AgentSpeak plans used by volunteers.
spade_venv/ - virtual environment with the SPADE/AgentSpeak/BDI
  dependencies.

PROGRAMMATIC USE
----------------

import scenarios

await scenarios.create_operator()            # starts the operator
scenarios.start_simulation(
    "default",
    ambulance_mode="reassign_bidding",       # any mode in agents.AMBULANCE_MODES
    volunteer_mode="bidding",
    ambulance_reposition="corners",
)
# ... let the simulation run ...
await scenarios.stop_simulation()

scenarios.AMBULANCE_MODES lists the available ambulance selection
modes, scenarios.VOLUNTEER_MODES the volunteer ones and
scenarios.AMBULANCE_REPOSITION_MODES the reposition ones (each maps to
the label shown in the UI).
