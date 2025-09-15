# MCTS node
from math import sqrt, log
from typing import Dict, List, Optional


class MCTSNode:
    def __init__(self, state_path: List[str], parent: Optional["MCTSNode"] = None):
        self.state_path: List[str] = state_path        
        self.parent: Optional[MCTSNode] = parent
        self.children: Dict[str, "MCTSNode"] = {}     
        self.n_sel: int = 0                           
        self.n_det: int = 0                           
        self.reward: float = 0.0                      
        self.best_seed: Optional[list] = None       

    # ----------  API --------- #
    def uct(self, rho: float, bias: float = 0.0) -> float:
        if self.n_sel == 0:
            return float("inf")
        # return self.n_det / self.n_sel + rho * sqrt(2 * log(self.parent.n_sel) / self.n_sel)
        return self.reward / self.n_sel + bias + rho * sqrt(2 * log(self.parent.n_sel) / self.n_sel)

    def has_child(self, state_name: str) -> bool:
        return state_name in self.children

    def add_child(self, state_name: str) -> "MCTSNode":
        child = MCTSNode(self.state_path + [state_name], self)
        self.children[state_name] = child
        return child

    def fully_expanded(self) -> bool:
        return all(c.n_sel > 0 for c in self.children.values())

    def add_reward(self, r: float):
        self.reward += r
        self.n_sel  += 1

    def to_dict(self):
        return {
            "state_path": self.state_path,
            "n_sel": self.n_sel,
            "n_det": self.n_det,
            "reward": self.reward,
            "children": {k: v.to_dict() for k, v in self.children.items()}
        }

    @classmethod
    def from_dict(cls, d, parent=None):
        node = cls(d["state_path"], parent)
        node.n_sel = d["n_sel"]
        node.n_det = d["n_det"]
        node.reward = d["reward"]
        for k, v in d["children"].items():
            node.children[k] = cls.from_dict(v, node)
        return node
