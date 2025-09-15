# MCTS schedule
import random, math
from math import sqrt
from collections import defaultdict, deque
from typing import List, Optional, Tuple
from .mcts_node import MCTSNode

MCTS_RHO = 1.4
STATE_REWARD = 1
TRANSITION_REWARD = 0.8
ERROR_REWARD = 0.4
FIELD_REWARD = 0.2
COV_BIAS = 1.2
DEPTH_GAMMA = 1.1
ALPHA_SINK = 0.15
EPSILON_ROOT = 0.10 
MAX_CONSECUTIVE_SELECTIONS = 10 


class MCTSSchedule:
    def __init__(self, init_state: str):
        self.root = MCTSNode([init_state])
        self.rho = MCTS_RHO
        self.state_reward = STATE_REWARD
        self.transition_reward = TRANSITION_REWARD
        self.error_reward = ERROR_REWARD
        self.field_reward = FIELD_REWARD
        self.state_visits = defaultdict(int)
        self.cov_bias = COV_BIAS
        self.depth_gamma = DEPTH_GAMMA
        self.sink_hits = defaultdict(int)  
        self.sink_states = set()           
        self.last_terminals = deque(maxlen=64)
        self.selection_counter = defaultdict(int) 

    def _succ(self, fsm, s: str):
        return sorted({t[3] for t in fsm.transitions if t[0] == s and t[3] != s})

    def _fully_expanded(self, node, fsm):
        s = node.state_path[-1]
        succ = self._succ(fsm, s)
        return len(node.children) >= len(succ)

    def _novelty_bias(self, state_name: str) -> float:
        visit_cnt = self.state_visits.get(state_name, 0)
        return self.cov_bias / sqrt(visit_cnt + 1)

    def _child_score(self, child: MCTSNode) -> float:
        b = self._novelty_bias(child.state_path[-1])
        u = child.uct(self.rho, b)
        pen = ALPHA_SINK * self.sink_hits.get(child.state_path[-1], 0)
        return u - pen

    def _reset_selection_counter(self):
        self.selection_counter.clear()

    # -------- Selection -------- #
    def _select(self, fsm) -> List[MCTSNode]:
        path = [self.root]
        node = self.root
        at_root = True
        # while node.fully_expanded() and node.children:
        while self._fully_expanded(node, fsm) and node.children:
            kids = list(node.children.values())
            if at_root and random.random() < EPSILON_ROOT:
                node = min(kids, key=lambda n: n.n_sel)
            else:
                node = max(kids, key=self._child_score)
            at_root = False
            # node = max(node.children.values(), key=lambda n: n.uct(self.rho, self._novelty_bias(n.state_path[-1])))
            path.append(node)

        for c in node.children.values():
            st = c.state_path[-1]
            b  = self._novelty_bias(st)
            print(f"[UCT] parent={node.state_path[-1]} -> {st}  n_sel={c.n_sel} visit={self.state_visits.get(st,0)} bias={b:.3f} uct={c.uct(self.rho, b):.3f}")

        return path

    # -------- Expansion -------- #
    def _expand(self, node: MCTSNode, outgoing_states: List[str]) -> MCTSNode:
        unseen = [s for s in outgoing_states if not node.has_child(s)]
        if unseen:
            # pick = min(unseen, key=lambda s: self.state_visits.get(s, 0))
            pool = [s for s in unseen if s not in self.sink_states] or unseen
            pick = min(pool, key=lambda s: (self.state_visits.get(s, 0), random.random()))
            print(f"[EXPAND] parent={node.state_path[-1]} -> pick={pick}")
            return node.add_child(pick)
        # return min(node.children.values(), key=lambda n: self.state_visits.get(n.state_path[-1], 0))
        return min(node.children.values(), key=lambda n: (self.state_visits.get(n.state_path[-1], 0), random.random())
        )    

    def choose_state(self, fsm, state_obj_map) -> Tuple[MCTSNode, List[MCTSNode]]:
        path = self._select(fsm)
        leaf = path[-1]

        curr_state_name = leaf.state_path[-1]
        outgoing = list({t[3] for t in fsm.transitions if t[0] == curr_state_name and t[3] != curr_state_name})


        if self.selection_counter[curr_state_name] >= MAX_CONSECUTIVE_SELECTIONS:
            print(f"[ANTI-STICKY] State {curr_state_name} selected too many times, selecting a different state.")

            alternate_node = random.choice([child for child in self.root.children.values() if child.state_path[-1] != curr_state_name])
            path = [self.root, alternate_node]
            leaf = alternate_node
            curr_state_name = leaf.state_path[-1]

        self.selection_counter[curr_state_name] += 1

        if self.selection_counter[curr_state_name] > MAX_CONSECUTIVE_SELECTIONS * 2:
            self._reset_selection_counter()

        # if outgoing and leaf.n_sel == 0 and not leaf.children:
        if outgoing and (leaf.n_sel == 0 or leaf.n_sel > 0):
            leaf = self._expand(leaf, outgoing)
            path.append(leaf)
        return leaf, path

    def _bounded_fields_gain(self, n, k=3.0):
        # g(n)=1-exp(-n/k)  ∈ [0,1]
        try:
            return 1.0 - math.exp(-float(n)/k)
        except Exception:
            return 0.0

    def _norm_weights(self):
        S = (self.state_reward + self.transition_reward +
            self.error_reward + self.field_reward)
        return (self.state_reward/S, self.transition_reward/S,
                self.error_reward/S, self.field_reward/S)

    # -------- Back-propagation -------- #
    def backpropagate(self, path: List[MCTSNode], new_state: bool = False, new_transition: bool = False, error_reward: float = 0.0, new_fields_cnt: int = 0):
        if path == None:
            print("path is None")
            return False
        print("backpropagage path:", path)
        ws, wt, we, wf = self._norm_weights()
        reward = ws * (1.0 if new_state else 0.0)
        reward += wt * (1.0 if new_transition else 0.0)
        reward += we * (error_reward)
        reward += wf * self._bounded_fields_gain(new_fields_cnt)
        reward = max(0.0, min(1.0, reward))
        # for node in path:
        #     node.add_reward(reward)
        path_len = len(path)
        if path_len == 0:
            return reward
        wl = [self.depth_gamma ** d for d in range(path_len)]
        for depth, node in enumerate(path):
            depth_reward = reward * (wl[depth] / float(sum(wl)))
            node.add_reward(depth_reward)

        # 动态标记 sink
        last = path[-1].state_path[-1] if path else None

        if last:
            self.last_terminals.append(last)
            if reward <= 1e-9:
                self.sink_hits[last] += 1  
            else:
                self.sink_hits[last] = max(0, self.sink_hits[last] - 1)

        print(f"[BP] last={last} reward={reward:.3f} sink_hits={self.sink_hits.get(last,0)}")

        return reward

    def path_from_fsm_path(self, fsm, path, *, verify: bool = False, allow_rebase: bool = True):
        ps = path.path_states
        acts = path.input_symbols
        outs = path.output_symbols
        print("path_states", ps)
        print("input_symbols", acts)
        print("output_symbols", outs)
        if not ps or not acts or len(ps) != len(acts) + 1:
            raise ValueError("Invalid Path: need path_states length = input_symbols length + 1")

        root_state = self.root.state_path[-1]
        start_idx = 0
        if ps[0] != root_state:
            if allow_rebase:
                try:
                    start_idx = ps.index(root_state)
                except ValueError:
                    print(f"[MCTS] path_from_fsm_path: path does not contain root '{root_state}', mapping from root anyway")
                    start_idx = 0
            else:
                raise ValueError(f"Path start_state '{ps[0]}' != MCTS root '{root_state}'")

        if verify and acts is not None:
            for i in range(start_idx, len(ps) - 1):
                src = ps[i]
                dst = ps[i + 1]
                act = acts[i - start_idx] if (i - start_idx) < len(acts) else None
                if act is None:
                    raise ValueError("Missing action while verifying transitions")
                if outs is not None and (i - start_idx) < len(outs) and outs[i - start_idx] is not None:
                    ret = outs[i - start_idx]
                    ok = any(t[0] == src and t[1] == act and t[2] == ret and t[3] == dst for t in fsm.transitions)
                else:
                    ok = any(t[0] == src and t[1] == act and t[3] == dst for t in fsm.transitions)
                if not ok:
                    raise ValueError(f"FSM has no transition for ({src}) --{act}--> ({dst})")

        node = self.root
        mcts_nodes = [node]
        for j in range(start_idx, len(ps) - 1):
            next_state = ps[j + 1]
            if not node.has_child(next_state):
                node = node.add_child(next_state)
            else:
                node = node.children[next_state]
            mcts_nodes.append(node)

        return mcts_nodes
