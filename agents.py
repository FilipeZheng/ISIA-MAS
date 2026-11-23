"""Agents, behaviours and the shared environment grid used by the simulation.

Every object in the world (people, ambulances, volunteers, hospitals) registers
itself in the module-level ``Environment`` grid, so all agents can see the
positions of the others.

The scenario-specific setup (which agents exist for which scenario) lives in
``scenarios.py``; this module only knows how the agents behave.
"""

import asyncio
import random

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, FSMBehaviour, State
from spade.message import Message


Environment_height = 15
Environment_width = 25

Environment = [[set() for j in range(Environment_width)] for i in range(Environment_height)]
Hospital_coords = set()

dirx = [0,1,0,-1,0]
diry = [1,0,-1,0,0]

# How the operator chooses which ambulance responds to a collapse.
AMBULANCE_MODE_CLOSEST = "closest"
AMBULANCE_MODE_REASSIGN = "reassign"
AMBULANCE_MODE_BIDDING = "bidding"
AMBULANCE_MODES = {
    AMBULANCE_MODE_CLOSEST: "Closest free ambulance (default)",
    AMBULANCE_MODE_REASSIGN: "Reassign if a closer ambulance becomes free",
    AMBULANCE_MODE_BIDDING: "Bidding (ambulances offer, best value wins)",
}

# Extra bid value for an ambulance that carries an AED.
AED_BID_BONUS = 10
# Bid penalty for an ambulance without an AED when one is required.
AED_MISSING_PENALTY = 100

# How the operator chooses which volunteer responds to a collapse.
VOLUNTEER_MODE_BIDDING = "bidding"
VOLUNTEER_MODE_CLOSEST = "closest"
VOLUNTEER_MODES = {
    VOLUNTEER_MODE_BIDDING: "Bidding (volunteers offer, closest wins)",
    VOLUNTEER_MODE_CLOSEST: "Closest available volunteer (no bidding)",
}

# Whether a free ambulance repositions itself before going idle again.
AMBULANCE_REPOSITION_OFF = "off"
AMBULANCE_REPOSITION_ON = "on"
AMBULANCE_REPOSITION_MODES = {
    AMBULANCE_REPOSITION_OFF: "Off (stay where it is)",
    AMBULANCE_REPOSITION_ON: "On (move to the spot furthest from other ambulances)",
}

# A collapse of this severity needs an AED, unless the responder carries one.
AED_REQUIRED_SEVERITY = 3


class SimulationMetrics:
    """Collect timing metrics for the collapses of one simulation."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._records = {}
        self._aid_times = []
        self._hospital_times = []

    def record_collapse(self, person, false_alarm=False):
        now = asyncio.get_running_loop().time()
        self._records[person] = {
            "collapse": now,
            "aid": None,
            "hospital": None,
            "false_alarm": bool(false_alarm),
        }

    def record_aid(self, person):
        """Record the first volunteer/ambulance aid for a collapsed person."""
        record = self._records.get(person)
        if record is None or record["aid"] is not None:
            return
        now = asyncio.get_running_loop().time()
        record["aid"] = now
        self._aid_times.append(now - record["collapse"])

    def record_hospital(self, person):
        """Record when a transported person arrives at the hospital."""
        record = self._records.get(person)
        if record is None or record["hospital"] is not None:
            return
        now = asyncio.get_running_loop().time()
        record["hospital"] = now
        self._hospital_times.append(now - record["collapse"])

    def has_data(self):
        return bool(self._records)

    @staticmethod
    def _stats(values):
        if not values:
            return {"count": 0, "sum": 0.0, "mean": 0.0, "max": 0.0}
        total = sum(values)
        return {
            "count": len(values),
            "sum": total,
            "mean": total / len(values),
            "max": max(values),
        }

    def summary(self):
        return {
            "aid": self._stats(self._aid_times),
            "hospital": self._stats(self._hospital_times),
        }


Metrics = SimulationMetrics()

def within_E(x,y):
    """
    Checks whether coordinates are going out of the environment.
    Returns 1 if the coordinates are still valid, 0 otherwise.
    """
    global Environment,Environment_height,Environment_width
    if (x<0 or x>=Environment_width or y<0 or y>=Environment_height): return 0
    return 1

class Hospital: #object
    def __init__(self,x,y,capacity=2):
        global Environment, Hospital_coords
        self.capacity=2
        self.x = x
        self.y = y
        Environment[y][x].add(self)
        Hospital_coords.add((x,y))
    #TODO (thing with patients (?), ...)

class RandomWalk(CyclicBehaviour): #Just testing
    async def run(self):
        agent = self.agent
        d = random.randint(0,4)
        await agent.walk(d)
        await asyncio.sleep(agent.walk_speed)

class WalkTo(CyclicBehaviour):
    async def run(self):
        agent = self.agent
        if agent.target_x==agent.x and agent.target_y==agent.y:
            self.kill(exit_code=10)
            return
        dx,dy = abs(agent.target_x-agent.x), abs(agent.target_y-agent.y)
        d = random.randint(1,dx+dy)<=dx
        if d:
            d += 2*(agent.target_x<agent.x)
        else:
            d += 2*(agent.target_y<agent.y)
        await agent.walk(d)
        await asyncio.sleep(agent.walk_speed)

class Collapsed(CyclicBehaviour):
    """A collapsed agent calling for help.

    The victim does not talk to the operator: it only asks the people in the
    environment for help, and one of them has to walk to it and report the
    collapse once it gets there.
    """

    async def run(self):
        if self.is_killed():
            return
        agent = self.agent
        for row in Environment:
            for cell in row:
                for other in cell:
                    # Only plain people (they carry a ByStand behaviour) react
                    # to this call; volunteers and ambulances are dispatched by
                    # the operator.
                    if other is agent or type(other) is not WalkingAgent:
                        continue
                    msg = Message(to=str(other.jid))
                    msg.set_metadata("performative","inform")
                    msg.body = f"HELP {agent.x} {agent.y}"
                    await self.send(msg)
        await asyncio.sleep(1)

def collapsed_victim_at(x, y, exclude=None):
    """Return a collapsed WalkingAgent at (x, y), if there is one."""
    if not within_E(x, y):
        return None
    for obj in Environment[y][x]:
        if (obj is not exclude and isinstance(obj, WalkingAgent)
                and getattr(obj, "collapsed", False)):
            return obj
    return None


class ByStand(CyclicBehaviour):
    async def run(self):
        agent = self.agent
        if self.is_killed() or agent.collapsed:
            return

        # A collapsed agent is calling for help: go there and report it.
        msg = await self.receive(timeout=1)
        if self.is_killed() or agent.collapsed:
            return
        if msg:
            msg_type,*body = msg.body.split()
            if msg_type=="HELP":
                try:
                    x,y = map(int,body)
                except ValueError:
                    pass
                else:
                    await self.go_and_report(x,y)

        # Also look around: a collapsed agent close to us also needs help.
        x,y=agent.x,agent.y
        for i in range(-2,3):
            for j in range(-2,3):
                nx,ny=x+i,y+j
                if collapsed_victim_at(nx, ny, exclude=agent) is not None:
                    await self.go_and_report(nx,ny)

    async def go_and_report(self,x,y):
        """Walk to (x,y), report the collapse and stay with the victim.

        Only one bystander looks after a given victim. The first one to arrive
        claims it; anybody still on the way gives up and goes back to what it
        was doing before (where it can notice another collapsed person).
        """
        agent = self.agent
        if self.is_killed() or agent.collapsed or not within_E(x,y):
            return

        victim = collapsed_victim_at(x, y, exclude=agent)
        if victim is None:
            return
        if victim.helper is not None and victim.helper is not agent:
            # Somebody is already looking after this victim.
            return

        await self._remember_initial_state()
        try:
            await agent.move_to(x,y,0.7)
            while not agent.walk_behaviour.is_killed():
                if self.is_killed() or agent.collapsed:
                    return
                if victim.helper not in (None, agent) or not victim.collapsed:
                    # Another bystander reached the victim first, or it is
                    # already being taken care of: give up and go back.
                    return
                await asyncio.sleep(0.5)

            # Only claim the victim if we really reached it and nobody else did.
            if self.is_killed() or agent.collapsed:
                return
            if not await agent.on_xy(x,y):
                return

            victim = collapsed_victim_at(x, y, exclude=agent)
            if victim is None:
                return
            if victim.helper is not None and victim.helper is not agent:
                return

            victim.helper = agent
            print(f"[{agent.jid}] Reached collapse at ({x}, {y}); staying with the patient")

            # Keep messaging the operator until it confirms that an ambulance
            # has been dispatched to this collapse.
            while (not self.is_killed()
                   and victim.collapsed
                   and victim in Environment[y][x]):
                msg = Message(to="operator@localhost")
                msg.set_metadata("performative","inform")
                msg.body = f"COLLAPSE {x} {y}"
                await self.send(msg)

                dispatched = False
                deadline = asyncio.get_running_loop().time() + 3
                while (not self.is_killed()
                       and asyncio.get_running_loop().time() < deadline):
                    reply = await self.receive(timeout=0.5)
                    if reply is not None and reply.body.startswith("AMBULANCE_DISPATCHED"):
                        dispatched = True
                        break

                if dispatched:
                    break
                if any(isinstance(k,EmergencyAmbulance) for k in Environment[y][x]):
                    break
                await asyncio.sleep(2)

            # Stay beside the collapsed person until the ambulance picks the
            # patient up (or the collapse is otherwise resolved).
            while (not self.is_killed()
                   and victim.collapsed
                   and victim in Environment[y][x]):
                await asyncio.sleep(1)
        finally:
            if victim is not None and victim.helper is agent:
                victim.helper = None
            await self._restore_initial_state()

    async def _remember_initial_state(self):
        """Remember where the agent was going before it was diverted."""
        agent = self.agent
        if getattr(agent, "diverted", False):
            return
        agent.diverted = True
        if (agent.target_x is not None and agent.target_y is not None
                and (agent.target_x, agent.target_y) != (agent.x, agent.y)):
            # Resume the walk the agent was already doing.
            agent.resume_target = (agent.target_x, agent.target_y, agent.walk_speed)
        else:
            # The agent was standing still: go back to that spot if it has to
            # leave a victim because somebody else is already with it.
            agent.resume_target = (agent.x, agent.y, agent.walk_speed)

    async def _restore_initial_state(self):
        """Resume the walk the agent was doing before helping a victim."""
        agent = self.agent
        if not getattr(agent, "diverted", False):
            return
        agent.diverted = False
        target, agent.resume_target = getattr(agent, "resume_target", None), None
        if target is not None:
            await agent.move_to(*target)

class CitSensor(CyclicBehaviour):
    async def run(self):
        agent = self.agent
        msg = None
        msg = await self.receive(timeout=10)
        if msg:
            msg_type,*body = msg.body.split()
            x, y =body
            if msg_type=="HELP":
                msg = Message(to="operator@localhost")
                msg.set_metadata("performative","inform")
                msg.body = f"COLLAPSE {x} {y}"
                await self.send(msg)

class AEDBehav(CyclicBehaviour):
    async def run(self):
        agent = self.agent
        msg = await self.receive(timeout=10)
        if msg:
            msg_type,*body = msg.body.split()
            if msg_type=="AED":
                reply = msg.make_reply() # inform availability
                reply.body = "RECEIVED" if agent.available else "NOT_AVAILABLE"
                await self.send(reply)
            if agent.available:
                bx, by = agent.base_x, agent.base_y
                tx, ty = body
                agent.available=False # become unavailable
                await agent.move_to(tx,ty,9) # move to the location needed
                while not agent.walk_behaviour.is_killed():
                    await asyncio.sleep(0.5)

                # add behaviour when colapse details are added

                agent.available=True # become available
                await agent.move_to(bx,by,7) # move to the base location
                while not agent.walk_behaviour.is_killed():
                   await asyncio.sleep(0.5)

class WalkingAgent(Agent):
    x=None
    y=None
    target_x=None
    target_y=None
    walk_speed=1    #Be aware that this is the wait time between steps, therefore less is faster
    walk_behaviour=None
    collapsed=0
    helper=None     #Bystander currently staying with this collapsed agent
    diverted=False  #1 while a bystander is walking towards a victim
    resume_target=None  #Walk to resume once the bystander is free again
    false_alarm=0   #1 when the collapse does not actually need an ambulance
    severity=1      #Higher values are more serious; 3 needs an AED
    # Only volunteers and ambulances can tell a false alarm from a real one.
    can_identify_collapse=False
    async def setup(self):
        pass
        #print(f"{self.jid} created.")
    async def on_xy(self,x,y):
        return self.x==x and self.y==y
    async def insert(self,x,y):     #This is only supposed to be used when placing the agents in the environment
        global Environment
        if self.x is not None:
            Environment[self.y][self.x].remove(self)
        self.x=x
        self.y=y
        Environment[y][x].add(self)
        self.add_behaviour(ByStand())
    async def remove(self): #For when the person is picked up
        global Environment
        for behav in self.behaviours:
            behav.kill()
        if self.x is not None:
            Environment[self.y][self.x].remove(self)
        self.x=None
        self.y=None
        self.helper=None
    # The following function is to move towards some coordinates, the function below is for walking just one step
    async def move_to(self,x,y,walk_speed=None):
        if self.walk_behaviour !=None and not self.walk_behaviour.is_killed():
            self.walk_behaviour.kill()
        self.target_x=x
        self.target_y=y
        self.walk_speed=walk_speed or self.walk_speed
        self.walk_behaviour = WalkTo()
        self.add_behaviour(self.walk_behaviour)
    async def walk(self,d): # d represents the direction the agent walks towards
        global dirx,diry,Environment,within_E
        nx=self.x+dirx[d]
        ny=self.y+diry[d]
        if within_E(nx,ny):
            Environment[self.y][self.x].remove(self)
            self.x=nx
            self.y=ny
            Environment[ny][nx].add(self)
    async def move_randomly(self,walk_speed=None):
        if self.walk_behaviour !=None and not self.walk_behaviour.is_killed():
            self.walk_behaviour.kill(exit_code=10)
        self.walk_speed=walk_speed or self.walk_speed
        self.walk_behaviour = RandomWalk()
        self.add_behaviour(self.walk_behaviour)
    async def at_hospital(self):
        return any(type(i)==Hospital for i in Environment[self.y][self.x])

    async def collapse(self, false_alarm=False, severity=1):
        """Collapse where the agent is.

        Args:
            false_alarm (bool): mark this as a false positive. It still looks
                like a collapse (bystanders report it and the operator reacts),
                but only a volunteer or an ambulance that reaches the agent can
                identify that no ambulance transport is needed.
            severity (int): how serious the collapse is. Severity 3 needs an
                AED unless the responding ambulance carries one.
        """
        for behav in self.behaviours:
            behav.kill()

        self.collapsed = 1
        self.false_alarm = 1 if false_alarm else 0
        self.severity = severity
        self.helper = None
        Metrics.record_collapse(self, false_alarm)
        # Call the people around for help; a bystander will report the collapse
        # to the operator once it gets here.
        self.add_behaviour(Collapsed())

    def identify_false_alarm(self, assessor):
        """Tell whether this collapse is a false alarm.

        Returns True/False for agents that can assess a collapse (volunteers and
        ambulances by default), and None when the assessor cannot tell or the
        agent is no longer collapsed.
        """
        if not self.collapsed or not getattr(assessor, "can_identify_collapse", False):
            return None
        return bool(self.false_alarm)

    async def recover(self):
        """Get back up after a false alarm has been identified."""
        if not self.collapsed:
            return
        for behav in self.behaviours:
            behav.kill()
        self.collapsed = 0
        self.false_alarm = 0
        self.severity = 1
        self.helper = None
        self.add_behaviour(ByStand())

Ambulances = set()

#Behaviour and state definitions for Ambulance Agent, idk if BDI is more appropriate
class Behaviour(FSMBehaviour):
    pass
idling = 'IDLING'
moving = 'MOVING'
helping = 'HELPING'
in_H = 'IN_H'

class Idling(State):
    async def run(self):
        agent = self.agent
        if (getattr(agent, "reposition", False)
                and getattr(agent, "reposition_pending", False)):
            if await self.reposition() == "HELP":
                self.set_next_state(moving)
                return
        await agent.move_randomly(4)
        while 1:
            msg = await self.receive()
            if msg!=None:
                msg_type,*body =msg.body.split()
                if msg_type=="HELP":
                    await agent.move_to(*map(int,body),0.5)
                    self.set_next_state(moving)
                    return
                if msg_type=="BID":
                    try:
                        x, y, severity = map(int, body)
                    except ValueError:
                        pass
                    else:
                        value = agent.bid_value(x, y, severity)
                        offer = Message(to="operator@localhost")
                        offer.set_metadata("performative", "inform")
                        offer.body = f"AMBULANCE_OFFER {x} {y} {value}"
                        await self.send(offer)
                        print(
                            f"[{agent.jid}] Bid {value} for ({x}, {y})"
                        )
                if msg_type=="ASSIGN":
                    try:
                        x, y = map(int, body)
                    except ValueError:
                        pass
                    else:
                        await agent.move_to(x, y, 0.5)
                        self.set_next_state(moving)
                        return
            await asyncio.sleep(1)

    async def reposition(self):
        """Ask the operator for a spot far from the other ambulances."""
        agent = self.agent
        agent.reposition_pending = False

        request = Message(to="operator@localhost")
        request.set_metadata("performative", "inform")
        request.body = f"REQUEST_POSITION {agent.x} {agent.y}"
        await self.send(request)

        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            msg = await self.receive(timeout=0.5)
            if msg is None:
                continue
            parts = msg.body.split()
            if len(parts) == 3 and parts[0] == "POSITION":
                try:
                    x, y = map(int, parts[1:])
                except ValueError:
                    return None
                if not within_E(x, y):
                    return None
                await agent.move_to(x, y)
                while not agent.walk_behaviour.is_killed():
                    await asyncio.sleep(0.5)
                print(f"[{agent.jid}] Repositioned to ({x}, {y})")
                return None
            if len(parts) == 1 and parts[0] == "STAY":
                return None
            if len(parts) == 3 and parts[0] == "HELP":
                try:
                    x, y = map(int, parts[1:])
                except ValueError:
                    return None
                await agent.move_to(x, y, 0.5)
                return "HELP"
        return None

class Moving(State):
    async def run(self):
        agent = self.agent
        while 1:
            # The operator may stand the ambulance down if the incident turns
            # out to be a false alarm (identified by another responder).
            msg = await self.receive(timeout=1)
            if msg is not None and msg.body.startswith("CANCEL"):
                parts = msg.body.split()
                if len(parts) == 3:
                    try:
                        x, y = map(int, parts[1:])
                    except ValueError:
                        pass
                    else:
                        if (x, y) == (agent.target_x, agent.target_y):
                            if not agent.walk_behaviour.is_killed():
                                agent.walk_behaviour.kill()
                            print(f"[{agent.jid}] Response to ({x}, {y}) cancelled")
                            agent.reposition_pending = True
                            self.set_next_state(idling)
                            return
            if agent.walk_behaviour.is_killed():
                report = Message(to="operator@localhost")
                report.set_metadata("performative","inform")
                report.body = f"AT_INCIDENT {agent.target_x} {agent.target_y}"
                await self.send(report)
                self.set_next_state(helping)
                return
            #put here code for receiving messages that change the state
            await asyncio.sleep(1)    
class Helping(State):
    async def run(self):
        agent= self.agent
        picked_up = False
        for i in Environment[agent.y][agent.x].copy():
            if not (isinstance(i,WalkingAgent) and i.collapsed):
                continue
            false_alarm = i.identify_false_alarm(agent)
            if false_alarm is None:
                continue
            Metrics.record_aid(i)
            if false_alarm:
                # No transport needed: let the patient get up and tell the
                # operator so any other responders can be stood down.
                print(f"[{agent.jid}] Identified a false alarm at ({agent.x}, {agent.y})")
                await i.recover()
                report = Message(to="operator@localhost")
                report.set_metadata("performative","inform")
                report.body = f"FALSE_ALARM {agent.x} {agent.y}"
                await self.send(report)
                continue
            await i.remove()
            agent.agents_on_board.add(i)
            picked_up = True

        if not picked_up:
            # There is nothing to transport (no patient, or only false alarms).
            agent.reposition_pending = True
            self.set_next_state(idling)
            return

        while 1:
            if agent.walk_behaviour.is_killed():
                # Hospital_coords may already be empty if the simulation is
                # being stopped, so only look for one when there is any.
                if Hospital_coords and not await agent.at_hospital():
                    x,y = agent.x, agent.y
                    nearest_H = min(Hospital_coords,key=lambda t:abs(t[0]-x)+abs(t[1]-y))
                    await agent.move_to(*nearest_H) #Change the coordinates to the nearest hospital
                    continue
                self.set_next_state(in_H)
                return
            #insert any other behaviour needed, such as receiving messages which would remove the need for the sleep below
            await asyncio.sleep(1)
class In_H(State):
    async def run(self):
        agent = self.agent
        # The patient has been delivered; the ambulance is free again.
        at_hospital = await agent.at_hospital()
        for patient in list(agent.agents_on_board):
            if at_hospital:
                Metrics.record_hospital(patient)
        agent.agents_on_board.clear()
        report = Message(to="operator@localhost")
        report.set_metadata("performative","inform")
        report.body = "IN_SERVICE"
        await self.send(report)
        print(f"[{agent.jid}] Back in service")
        agent.reposition_pending = True
        self.set_next_state(idling)

class EmergencyAmbulance(WalkingAgent):
    #Not finished yet
    can_identify_collapse = True

    #attribute definitions
    behav = None
    has_aed = False

    def __init__(self, jid, password, has_aed=False, reposition=False):
        super().__init__(jid, password)
        self.behav = None
        self.agents_on_board = set()
        self.has_aed = has_aed
        self.reposition = reposition
        self.reposition_pending = False

    def needs_aed_agent(self, severity):
        """Whether a separate AED agent is needed for a collapse."""
        return severity >= AED_REQUIRED_SEVERITY and not self.has_aed

    def bid_value(self, x, y, severity=1):
        """Return this ambulance's bid for an incident; higher is better."""
        distance = abs(self.x - x) + abs(self.y - y)
        value = -distance
        if self.has_aed:
            value += AED_BID_BONUS
        elif severity >= AED_REQUIRED_SEVERITY:
            value -= AED_MISSING_PENALTY
        return value
    #add other things to put in this


    async def setup(self):
        pass
    async def insert(self,x,y):     #This is only supposed to be used when placing the agents in the environment
        global Environment,Ambulances
        global Behaviour,Idling,Moving,Helping,In_H
        Ambulances.add(self)
        if self.x is not None:
            Environment[self.y][self.x].remove(self)
        self.x=x
        self.y=y
        Environment[y][x].add(self)
        if self.behav==None:
            behav = Behaviour()
            behav.add_state(name=idling, state=Idling(),initial=True)
            behav.add_state(name=moving, state=Moving())
            behav.add_state(name=helping, state=Helping())
            behav.add_state(name=in_H, state=In_H())
            behav.add_transition(source=idling,dest=moving)
            behav.add_transition(source=moving,dest=helping)
            behav.add_transition(source=moving,dest=idling)
            behav.add_transition(source=helping,dest=in_H)
            behav.add_transition(source=helping,dest=idling)
            behav.add_transition(source=in_H,dest=idling)
            self.behav = behav
            self.add_behaviour(self.behav)

class AEDAgent(WalkingAgent):

    #attribute definitions
    behav = None
    base_x = None
    base_y = None
    available = False
    #add other things to put in this


    async def setup(self):
        pass
    async def insert(self,x,y):     #This is only supposed to be used when placing the agents in the environment
        global Environment
        if self.x is not None:
            Environment[self.y][self.x].remove(self)
        self.base_x=x
        self.x=x
        self.base_y=y
        self.y=y
        self.available=True
        Environment[y][x].add(self)
        if self.behav==None:
            self.add_behaviour(AEDBehav())





Volunteers = set()


class VolunteerFSM(FSMBehaviour):
    class Available(State):
        async def run(self):
            volunteer = self.agent
            if not volunteer.on_duty:
                self.set_next_state("OFF_DUTY")
                return

            msg = await self.receive(timeout=1)
            if msg:
                parts = msg.body.split()
                if len(parts) == 3 and parts[0] == "HELP":
                    try:
                        x, y = map(int, parts[1:])
                    except ValueError:
                        pass
                    else:
                        distance = volunteer.consider_incident(x, y)
                        if distance is not None and (x, y) not in volunteer.offered:
                            volunteer.offered.add((x, y))
                            offer = Message(to="operator@localhost")
                            offer.set_metadata("performative", "inform")
                            offer.body = f"OFFER {x} {y} {distance}"
                            await self.send(offer)
                            print(f"[{volunteer.jid}] Offering help at {(x, y)}, distance {distance}")

                elif len(parts) == 3 and parts[0] == "ASSIGN":
                    try:
                        x, y = map(int, parts[1:])
                    except ValueError:
                        pass
                    else:
                        coords = (x, y)
                        # With bidding the volunteer must have offered for this
                        # incident first. Without bidding the operator picks
                        # the volunteer and assigns it directly.
                        accepts = coords in volunteer.offered or not volunteer.bidding
                        if accepts and volunteer.can_respond():
                            volunteer.current_assign = coords
                            volunteer.intention = {"goal": "assist_patient", "target": coords}
                            print(f"[{volunteer.jid}] Accepted assignment {coords}")
                            self.set_next_state("MOVING")
                            return

            self.set_next_state("AVAILABLE")

    class Moving(State):
        async def run(self):
            volunteer = self.agent
            target = volunteer.current_assign
            if target is None:
                self.set_next_state("AVAILABLE")
                return

            await volunteer.move_to(*target)
            print(f"[{volunteer.jid}] Moving towards {target}")
            steps = abs(volunteer.x - target[0]) + abs(volunteer.y - target[1])
            deadline = asyncio.get_running_loop().time() + max(10, steps * (volunteer.walk_speed + 1) * 2)
            while (volunteer.x, volunteer.y) != target:
                if volunteer.walk_behaviour.is_killed() or asyncio.get_running_loop().time() > deadline:
                    print(f"[{volunteer.jid}] Could not reach {target}")
                    if not volunteer.walk_behaviour.is_killed():
                        volunteer.walk_behaviour.kill()
                    volunteer.finish_assignment()
                    self.set_next_state("AVAILABLE")
                    return
                await asyncio.sleep(0.25)
            self.set_next_state("HELPING")

    class Helping(State):
        async def run(self):
            volunteer = self.agent
            target = volunteer.current_assign
            if target is None:
                self.set_next_state("AVAILABLE")
                return

            x, y = target
            patient = next((obj for obj in Environment[y][x].copy()
                            if obj is not volunteer and isinstance(obj, WalkingAgent)
                            and getattr(obj, "collapsed", False)), None)
            if patient is None:
                print(f"[{volunteer.jid}] No collapsed patient at {target}")
                volunteer.finish_assignment()
                self.set_next_state("AVAILABLE")
                return

            false_alarm = patient.identify_false_alarm(volunteer)
            if false_alarm is None:
                print(f"[{volunteer.jid}] Patient at {target} was already handled")
                volunteer.finish_assignment()
                self.set_next_state("AVAILABLE")
                return
            Metrics.record_aid(patient)
            if false_alarm:
                # A volunteer can handle a false alarm alone: the patient gets
                # up and no ambulance intervention is needed.
                print(f"[{volunteer.jid}] Identified a false alarm at {target}")
                await patient.recover()
                report = Message(to="operator@localhost")
                report.set_metadata("performative", "inform")
                report.body = f"FALSE_ALARM {x} {y}"
                await self.send(report)
                volunteer.finish_assignment()
                self.set_next_state("AVAILABLE")
                return

            volunteer.beliefs["patient_confirmed"] = True
            print(f"[{volunteer.jid}] Confirmed collapsed patient at {target}")
            report = Message(to="operator@localhost")
            report.set_metadata("performative", "inform")
            report.body = f"CONFIRMED {x} {y}"
            await self.send(report)

            if volunteer.has_firstAid:
                patient.receiving_first_aid = True  # Simulated intervention only
                print(f"[{volunteer.jid}] Providing simulated first aid")
            else:
                print(f"[{volunteer.jid}] Monitoring patient until ambulance arrives")

            # Wait for the ambulance to pick up the patient or reach the cell.
            while True:
                cell = Environment[y][x]
                if patient not in cell or not getattr(patient, "collapsed", False):
                    break
                if any(isinstance(obj, EmergencyAmbulance) for obj in cell):
                    break
                await asyncio.sleep(1)

            print(f"[{volunteer.jid}] Handover completed at {target}")
            volunteer.finish_assignment()
            self.set_next_state("AVAILABLE")

    class OffDuty(State):
        async def run(self):
            await asyncio.sleep(1)
            self.set_next_state("AVAILABLE" if self.agent.on_duty else "OFF_DUTY")


class VolunteerAgent(WalkingAgent):
    can_identify_collapse = True

    def __init__(self, jid, password, status="AVAILABLE", training="basic",
                 has_firstAid=True, max_distance=30, bidding=True):
        super().__init__(jid, password)
        self.x, self.y = None, None
        self.target_x, self.target_y = None, None
        self.walk_behaviour = None
        self.walk_speed = 1
        self.on_duty = status == "AVAILABLE"
        self.training = training
        self.has_firstAid = has_firstAid
        self.max_distance = max_distance
        self.bidding = bidding
        self.current_assign = None
        self.offered = set()
        self.beliefs = {"on_duty": self.on_duty, "known_incidents": {}, "patient_confirmed": False}
        self.desires = ["respond_if_capable", "provide_first_aid", "avoid_duplicate_response"]
        self.intention = None

    def can_respond(self):
        return (self.on_duty and self.current_assign is None
                and self.x is not None and self.y is not None
                and self.training not in (None, "none", "untrained"))

    def consider_incident(self, x, y):
        if not self.can_respond() or not within_E(x, y):
            return None
        distance = abs(self.x - x) + abs(self.y - y)
        self.beliefs["known_incidents"][(x, y)] = {"distance": distance}
        return distance if distance <= self.max_distance else None

    def finish_assignment(self):
        self.current_assign = None
        self.intention = None
        self.beliefs["patient_confirmed"] = False

    async def insert(self, x, y):
        # Do not attach the default ByStand behaviour to a volunteer.
        if not within_E(x, y):
            raise ValueError("Volunteer position outside the grid")
        if self.x is not None:
            Environment[self.y][self.x].discard(self)
        self.x, self.y = x, y
        Environment[y][x].add(self)
        Volunteers.add(self)


    async def setup(self):
        fsm = VolunteerFSM()

        fsm.add_state("AVAILABLE", VolunteerFSM.Available(), initial=self.on_duty)
        fsm.add_state("MOVING", VolunteerFSM.Moving())
        fsm.add_state("HELPING", VolunteerFSM.Helping())
        fsm.add_state("OFF_DUTY", VolunteerFSM.OffDuty(), initial=not self.on_duty)

        fsm.add_transition(source="AVAILABLE", dest="AVAILABLE")
        fsm.add_transition(source="AVAILABLE", dest="MOVING")
        fsm.add_transition(source="AVAILABLE", dest="OFF_DUTY")
        fsm.add_transition(source="MOVING", dest="HELPING")
        fsm.add_transition(source="MOVING", dest="AVAILABLE")
        fsm.add_transition(source="HELPING", dest="AVAILABLE")
        fsm.add_transition(source="OFF_DUTY", dest="OFF_DUTY")
        fsm.add_transition(source="OFF_DUTY", dest="AVAILABLE")

        self.add_behaviour(fsm)



class Operator_Behav(CyclicBehaviour):
    async def run(self):
        operator = self.agent

        # Drain the messages that are already available. This lets the
        # operator deal with several collapses reported at the same time
        # instead of handling only one of them per cycle.
        first = True
        for _ in range(50):
            msg = await self.receive(timeout=0.5 if first else 0)
            first = False
            if msg is None:
                break
            await self.handle_message(msg)

        # Select the closest volunteer after collecting offers.
        now = asyncio.get_running_loop().time()

        for coords, incident in list(operator.pending_offers.items()):
            if now < incident["deadline"]:
                continue

            if incident["offers"]:
                distance, volunteer_jid = min(incident["offers"])

                assignment = Message(to=volunteer_jid)
                assignment.set_metadata("performative", "inform")
                assignment.body = f"ASSIGN {coords[0]} {coords[1]}"
                await self.send(assignment)

                print(f"[Operator] Assigned {coords} to {volunteer_jid}")
            else:
                print(f"[Operator] No available volunteer for {coords}")

            del operator.pending_offers[coords]

        # Select the ambulance with the best bid after collecting offers.
        now = asyncio.get_running_loop().time()
        for coords, incident in list(operator.pending_ambulance_offers.items()):
            if now < incident["deadline"]:
                continue

            offers = [
                (value, ambulance_jid)
                for value, ambulance_jid in incident["offers"]
                if ambulance_jid not in operator.busy_ambulances
            ]

            if offers:
                value, ambulance_jid = max(offers)

                assignment = Message(to=ambulance_jid)
                assignment.set_metadata("performative", "inform")
                assignment.body = f"ASSIGN {coords[0]} {coords[1]}"
                await self.send(assignment)

                operator.dispatched_ambulances[coords] = ambulance_jid
                operator.busy_ambulances.add(ambulance_jid)
                print(
                    f"[Operator] Assigned {coords} to {ambulance_jid} "
                    f"with bid {value}"
                )
            else:
                print(f"[Operator] No ambulance bid for {coords}")
                await self.dispatch_closest_ambulance(coords)

            del operator.pending_ambulance_offers[coords]

    async def handle_message(self, msg):
        operator = self.agent
        parts = msg.body.split()

        if len(parts) == 3 and parts[0] == "COLLAPSE":
            await self.handle_collapse(msg, parts)

        elif len(parts) == 4 and parts[0] == "OFFER":
            try:
                x, y, distance = map(int, parts[1:])
            except ValueError:
                return

            incident = operator.pending_offers.get((x, y))

            if incident is not None:
                incident["offers"].append(
                    (distance, str(msg.sender))
                )

        elif len(parts) == 4 and parts[0] == "AMBULANCE_OFFER":
            try:
                x, y, value = map(int, parts[1:])
            except ValueError:
                return

            incident = operator.pending_ambulance_offers.get((x, y))

            if incident is not None:
                incident["offers"].append(
                    (value, str(msg.sender))
                )

        elif len(parts) == 3 and parts[0] == "CONFIRMED":
            print(f"[Operator] Volunteer confirmed {parts[1:]}")

        elif len(parts) == 3 and parts[0] == "FALSE_ALARM":
            try:
                x, y = map(int, parts[1:])
            except ValueError:
                return

            coords = (x, y)
            # No volunteer or ambulance assignment is needed any more...
            operator.pending_offers.pop(coords, None)
            operator.pending_ambulance_offers.pop(coords, None)
            # ...and an ambulance that is already on its way can be stood
            # down (it is not needed for a false alarm).
            ambulance_jid = operator.dispatched_ambulances.pop(coords, None)
            if ambulance_jid is not None:
                operator.busy_ambulances.discard(ambulance_jid)
                cancel = Message(to=ambulance_jid)
                cancel.set_metadata("performative", "inform")
                cancel.body = f"CANCEL {x} {y}"
                await self.send(cancel)
            print(f"[Operator] False alarm at {coords}")
            # The stood-down ambulance is free again, so it may now be a
            # better option for another incident.
            await self.rebalance_ambulances()

        elif len(parts) == 3 and parts[0] == "AT_INCIDENT":
            try:
                x, y = map(int, parts[1:])
            except ValueError:
                return
            operator.handled_incidents.add((x, y))
            print(f"[Operator] Ambulance reached ({x}, {y})")

        elif len(parts) == 1 and parts[0] == "IN_SERVICE":
            await self.release_ambulance(str(msg.sender))

        elif len(parts) == 3 and parts[0] == "REQUEST_POSITION":
            try:
                x, y = map(int, parts[1:])
            except ValueError:
                return
            await self.handle_reposition_request(msg, (x, y))

    async def handle_collapse(self, msg, parts):
        operator = self.agent
        try:
            x, y = map(int, parts[1:])
        except ValueError:
            return
        if not within_E(x, y):
            return

        coords = (x, y)

        # Tell the volunteers about a new incident only once, but keep trying
        # to dispatch an ambulance on every report (a bystander repeats its
        # messages until it gets one).
        if coords not in operator.reported_incidents:
            operator.reported_incidents.add(coords)

            if operator.volunteer_mode == VOLUNTEER_MODE_BIDDING:
                # Give volunteers 3 seconds to send their offers.
                operator.pending_offers[coords] = {
                    "deadline": asyncio.get_running_loop().time() + 3,
                    "offers": []
                }

                # Notify every volunteer currently on duty.
                for volunteer in list(Volunteers):
                    if volunteer.on_duty:
                        alert = Message(to=str(volunteer.jid))
                        alert.set_metadata("performative", "inform")
                        alert.body = f"HELP {x} {y}"
                        await self.send(alert)
            else:
                # No bidding: the operator directly assigns the closest
                # available volunteer.
                await self.assign_closest_volunteer(coords)

        dispatched = await self.dispatch_ambulance(coords)

        reply = msg.make_reply()
        if dispatched:
            reply.body = f"AMBULANCE_DISPATCHED {x} {y}"
        else:
            reply.body = f"RECEIVED {x} {y}"
        await self.send(reply)

    async def assign_closest_volunteer(self, coords):
        """Assign the closest available volunteer without waiting for offers."""
        operator = self.agent
        x, y = coords

        closest = min(
            (
                volunteer for volunteer in Volunteers
                if volunteer.on_duty and volunteer.can_respond()
                and volunteer.x is not None and volunteer.y is not None
                and abs(volunteer.x - x) + abs(volunteer.y - y)
                <= volunteer.max_distance
            ),
            key=lambda volunteer: abs(volunteer.x - x) + abs(volunteer.y - y),
            default=None,
        )

        if closest is None:
            print(f"[Operator] No available volunteer for {coords}")
            return False

        assignment = Message(to=str(closest.jid))
        assignment.set_metadata("performative", "inform")
        assignment.body = f"ASSIGN {x} {y}"
        await self.send(assignment)
        print(f"[Operator] Assigned {coords} to {closest.jid} (no bidding)")
        return True

    async def dispatch_ambulance(self, coords):
        """Send the best free ambulance to an incident.

        In bidding mode the free ambulances are asked for an offer first.
        Returns True if an ambulance is on its way (or bidding has started)
        for ``coords``.
        """
        operator = self.agent
        x, y = coords

        if coords in operator.handled_incidents:
            return True
        if coords in operator.dispatched_ambulances:
            return True

        busy = operator.busy_ambulances

        if operator.ambulance_mode == AMBULANCE_MODE_BIDDING:
            if coords in operator.pending_ambulance_offers:
                return True

            free = [
                a for a in Ambulances
                if a.x is not None and a.y is not None
                and str(a.jid) not in busy
            ]
            if not free:
                return False

            victim = collapsed_victim_at(x, y)
            severity = getattr(victim, "severity", 1) if victim is not None else 1
            operator.pending_ambulance_offers[coords] = {
                "deadline": asyncio.get_running_loop().time() + 3,
                "offers": []
            }
            for ambulance in free:
                bid = Message(to=str(ambulance.jid))
                bid.set_metadata("performative", "inform")
                bid.body = f"BID {x} {y} {severity}"
                await self.send(bid)
            print(
                f"[Operator] Requested ambulance bids for {coords} "
                f"(severity {severity})"
            )
            return True

        return await self.dispatch_closest_ambulance(coords)

    async def dispatch_closest_ambulance(self, coords):
        """Assign the closest free ambulance directly (also used as fallback)."""
        operator = self.agent
        x, y = coords

        if coords in operator.handled_incidents:
            return True
        if coords in operator.dispatched_ambulances:
            return True

        busy = operator.busy_ambulances
        nearest = min(
            (a for a in Ambulances
             if a.x is not None and a.y is not None
             and str(a.jid) not in busy),
            key=lambda a: abs(a.x - x) + abs(a.y - y),
            default=None
        )

        if nearest is None:
            return False

        await self.alert_ambulance(nearest, x, y)
        return True

    async def alert_ambulance(self, ambulance, x, y):
        """Tell one ambulance to respond to an incident and mark it busy."""
        operator = self.agent
        jid = str(ambulance.jid)
        operator.dispatched_ambulances[(x, y)] = jid
        operator.busy_ambulances.add(jid)
        alert = Message(to=jid)
        alert.set_metadata("performative", "inform")
        alert.body = f"HELP {x} {y}"
        await self.send(alert)
        print(f"[Operator] Dispatched ambulance {jid} to {(x, y)}")

    async def release_ambulance(self, jid):
        """Mark an ambulance as available again and rebalance incidents."""
        operator = self.agent
        operator.busy_ambulances.discard(jid)
        for coords, assigned in list(operator.dispatched_ambulances.items()):
            if assigned == jid:
                del operator.dispatched_ambulances[coords]
        print(f"[Operator] Ambulance {jid} is available again")
        await self.rebalance_ambulances()

    async def handle_reposition_request(self, msg, coords):
        """Tell a free ambulance where to wait, away from the others."""
        operator = self.agent
        sender = next(
            (a for a in Ambulances if str(a.jid) == str(msg.sender)), None
        )
        reply = msg.make_reply()
        reply.set_metadata("performative", "inform")

        if operator.ambulance_reposition != AMBULANCE_REPOSITION_ON or sender is None:
            reply.body = "STAY"
            await self.send(reply)
            return

        others = [
            a for a in Ambulances
            if a is not sender and a.x is not None and a.y is not None
        ]
        target = self.furthest_from_ambulances(others)
        if target is None:
            reply.body = "STAY"
        else:
            reply.body = f"POSITION {target[0]} {target[1]}"
            print(
                f"[Operator] Repositioning {sender.jid} from {coords} "
                f"to {target}"
            )
        await self.send(reply)

    @staticmethod
    def furthest_from_ambulances(ambulances):
        """Return the grid cell furthest from its nearest ambulance.

        When several cells are equally far away, the first one found (top to
        bottom, left to right) is used.
        """
        if not ambulances:
            return None

        best = None
        best_distance = -1
        for y in range(Environment_height):
            for x in range(Environment_width):
                nearest = min(
                    abs(x - a.x) + abs(y - a.y) for a in ambulances
                )
                if nearest > best_distance:
                    best_distance = nearest
                    best = (x, y)
        return best

    async def rebalance_ambulances(self):
        """Reassign incidents to closer ambulances that became free.

        This is only used in the "reassign" mode. For every incident whose
        assigned ambulance is still on its way, if a free ambulance is
        currently closer to the victim it takes over the response; the
        previous ambulance is told to stop going there.
        """
        operator = self.agent
        if operator.ambulance_mode != AMBULANCE_MODE_REASSIGN:
            return

        free = [
            a for a in Ambulances
            if a.x is not None and a.y is not None
            and str(a.jid) not in operator.busy_ambulances
        ]
        if not free:
            return

        for coords, assigned_jid in list(operator.dispatched_ambulances.items()):
            if coords in operator.handled_incidents:
                continue
            if assigned_jid not in operator.busy_ambulances:
                continue

            assigned = next(
                (a for a in Ambulances if str(a.jid) == assigned_jid), None
            )
            if assigned is None or assigned.x is None or assigned.y is None:
                continue

            x, y = coords
            current_distance = abs(assigned.x - x) + abs(assigned.y - y)
            if current_distance == 0:
                # It is already at the victim; do not pull it away.
                continue

            best = min(
                free,
                key=lambda a: abs(a.x - x) + abs(a.y - y),
                default=None
            )
            if best is None:
                continue

            best_distance = abs(best.x - x) + abs(best.y - y)
            if best_distance >= current_distance:
                continue

            # A clearly closer ambulance is free: switch the response.
            free.remove(best)
            operator.busy_ambulances.discard(assigned_jid)
            operator.busy_ambulances.add(str(best.jid))
            operator.dispatched_ambulances[coords] = str(best.jid)

            cancel = Message(to=assigned_jid)
            cancel.set_metadata("performative", "inform")
            cancel.body = f"CANCEL {x} {y}"
            await self.send(cancel)

            alert = Message(to=str(best.jid))
            alert.set_metadata("performative", "inform")
            alert.body = f"HELP {x} {y}"
            await self.send(alert)

            print(
                f"[Operator] Reassigned {coords} from {assigned_jid} "
                f"to closer ambulance {best.jid}"
            )

class Operator(Agent):
    async def setup(self):
        self.reported_incidents = set()
        self.pending_offers = {}
        self.pending_ambulance_offers = {}
        self.dispatched_ambulances = {}
        self.busy_ambulances = set()
        self.handled_incidents = set()
        self.ambulance_mode = AMBULANCE_MODE_CLOSEST
        self.volunteer_mode = VOLUNTEER_MODE_BIDDING
        self.ambulance_reposition = AMBULANCE_REPOSITION_OFF
        self.add_behaviour(Operator_Behav())
