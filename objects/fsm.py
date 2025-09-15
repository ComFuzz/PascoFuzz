import json
from objects.oracle import Oracle
from objects.power_schedule import Seed
import random, math

# +++
LAMBDA_LEN = 0.2
C_UCB      = 1.2 
EPS_EXP    = 0.2 

# path class in state
class Path:
    def __init__(self, path_states: list, input_symbols: list, output_symbols: list):
        self.path_states = path_states
        self.input_symbols = input_symbols
        self.output_symbols = output_symbols
        self.count = 0
        self.succ = 0
    
    @classmethod
    def from_json(cls, path_states: list, input_symbols: list, output_symbols: list, count: int, succ: int):
        new_path = cls(path_states, input_symbols, output_symbols)
        new_path.count = count
        new_path.succ = succ
        return new_path

    def add_count(self):
        self.count += 1

    def add_succ(self):
        self.succ += 1

# state class in FSM
class State(Seed):
    def __init__(self, name: str, paths: list):
        super().__init__()
        self.name = name
        self.paths = paths
        self.is_init = False
        self.oracle = Oracle()
        # +++ 
        self.visited = False
    
    # +++ 
    def set_visited(self):
        self.visited = True

    @classmethod
    # def from_json(cls, energy: float, adjusted_energy: float, count: int, name: str, paths: list, is_init: bool, p_state: str):
    def from_json(cls, energy: float, adjusted_energy: float, count: int, name: str, paths: list, is_init: bool, p_state: str, visited: bool=False):
        new_state = cls(name, paths)
        new_state.energy = energy
        new_state.adjusted_energy = adjusted_energy
        new_state.count = count
        new_state.is_init = is_init
        new_state.oracle.state = p_state
        # +++ 导入状态访问标识
        new_state.visited = visited
        return new_state
    
    def add_path(self, path):
        self.paths.append(path)

    def is_existed_path(self, new_path):
        for existed_path in self.paths:
            if existed_path.path_states == new_path:
                return True
        return False

    def select_path(self):
        if self.paths == []:
            return None

        # +++
        try_num = sum(max(1, p.count) for p in self.paths)

        def score(p):
            path_len = max(1, len(p.input_symbols))
            succ_score = p.succ / p.count if p.count > 0 else 0.0      
            len_score = LAMBDA_LEN * 1.0 / path_len                            
            count_score = C_UCB * math.sqrt(math.log(max(1, try_num)) / max(1, p.count))
            return succ_score + len_score + count_score

        if random.random() < EPS_EXP:
            return min(self.paths, key=lambda p: max(1, len(p.input_symbols)))
        
        selected_path = max(self.paths, key=score)
        selected_path.add_count()
        self.count += 1
        return selected_path


# FSM class
class FSM:
    def __init__(self, states: list, init_state: str, transitions: list):
        self.states = states
        self.init_state = init_state
        self.transitions = transitions
        self.new_state_count = 0
        self.edge_hits = {}

    def add_new_state(self):
        new_state = State("H"+str(self.new_state_count), [])
        self.new_state_count += 1
        self.states.append(new_state)
        return new_state

    def search_transition(self, start_state: str, input_sym: str, output_sym: str):
        for transition in self.transitions:
            if transition[0] == start_state and transition[1] == input_sym and transition[2] == output_sym:
                return True
        return False
    
    def search_new_transition(self, start_state: str, input_sym: str, output_sym: str):
        if self.search_transition(start_state, input_sym, output_sym):
            return True
        else:
            for transition in self.transitions:
                if ":" in transition[1]:
                    if transition[0] == start_state and input_sym in transition[1] and transition[2] == output_sym:
                        return True
        return False

    def get_state(self, name: str):
        for state in self.states:
            if state.name == name:
                return state
        return None
    
    def get_state_names(self):
        state_names = []
        for state in self.states:
            state_names.append(state.name)
        return state_names
    
    def refresh_paths(self):
        from fsm_helper import get_trace_from_path
        for state in self.states:
            for path in state.paths:
                path.input_symbols, path.output_symbols = get_trace_from_path(self, path.path_states)
                # print("path.input_symbols:", path.input_symbols)
    
    # +++ 
    def get_state_coverage(self):
        total = len(self.states)
        covered = sum(1 for s in self.states if s.visited)
        return covered, total, covered / total if total else 0.0
    
    # +++ 
    def _edge_key(self, src, inp, out, dst):
        return (src, inp, out, dst if dst is not None else None)

    def mark_edge(self, src: str, inp: str, out: str, dst: str | None):
        k = self._edge_key(src, inp, out, dst)
        self.edge_hits[k] = self.edge_hits.get(k, 0) + 1

    def mark_edges_from_seq(self, state_seq: list, input_seq: list, ret_seq: list):
        n = min(len(state_seq) - 1, len(input_seq), len(ret_seq))
        for i in range(n):
            src = state_seq[i]
            dst = state_seq[i + 1]
            inp = input_seq[i]
            out = ret_seq[i]
            self.mark_edge(src, inp, out, dst)

    def _edge_hits_as_list(self):
        return [[src, inp, out, dst, cnt] for (src, inp, out, dst), cnt in self.edge_hits.items()]


    def get_edge_hits_set(self):
        return {k for k, v in self.edge_hits.items() if v > 0}

    def _all_edge_keys(self):
        keys = set()
        for t in self.transitions:
            if len(t) >= 4:
                keys.add(self._edge_key(t[0], t[1], t[2], t[3]))
            else:
                keys.add(self._edge_key(t[0], t[1], t[2], None))
        return keys

    def get_edge_coverage(self, hits: set[tuple] | None = None):
        all_edges = self._all_edge_keys()
        if not all_edges:
            return 0, 0, 0.0
        if hits is None:
            hits = self.get_edge_hits_set()
        covered = len(all_edges & hits)
        total = len(all_edges)
        return covered, total, covered / total

    def to_json(self):
        data = {
            "states": self.states,
            "init_state": self.init_state,
            "transitions": self.transitions,
            "new_state_count": self.new_state_count,
            "edge_hits": self._edge_hits_as_list(), 
        }
        return json.dumps(data, default=lambda o: o.__dict__, indent=4)

        # return json.dumps(self, default=lambda o: o.__dict__, indent=4)
    
    @classmethod
    def from_json(cls, fsm_json):
        fsm_dict = json.loads(fsm_json)
        states = []
        for state in fsm_dict['states']:
            paths = []
            for path in state['paths']:
                # paths.append(Path.from_json(path['path_states'], path['input_symbols'], path['output_symbols'], path['count']))
                paths.append(Path.from_json(path['path_states'], path['input_symbols'], path['output_symbols'], path['count'], path['succ']))
            # states.append(State.from_json(state['energy'], state['adjusted_energy'], state['count'], state['name'], paths, state['is_init'], state['oracle']['state']))
            # +++ 
            states.append(State.from_json(state['energy'], state['adjusted_energy'], state['count'], state['name'], paths, state['is_init'], state['oracle']['state'], state.get('visited', False)))
        fsm = FSM(states, fsm_dict['init_state'], fsm_dict['transitions'])
        fsm.new_state_count = fsm_dict['new_state_count']
        f_edge = fsm_dict.get("edge_hits", [])
        fsm.edge_hits = {}
        for rec in f_edge:
            src, inp, out, dst, cnt = rec
            fsm.edge_hits[(src, inp, out, dst)] = cnt
        return fsm