"""

折叠节点函数：合并线性节点，同时满足以下约束：

折叠规则：
- 优先判断：如果 u->v 之间任意一条边属于受保护类型（例如合约之间的边），则不可合并。
- 必要条件：v 的所有入边来源必须全部来自 u（也就是说除了 u 之外没有其它节点指向 v）。
  这样可以防止把原先指向 v 的边错误地重定向到 u，造成不可区分的执行起点。此时将子节点 v 合并到父节点 u。
- 折叠动作：
  - 将 v 的 instructions 拼接到 u（保持执行顺序 u + v），更新 u 的 end_pc、terminator 等终态信息。
  - 将 v 的出边（v->x，其中 x 不在 {u, v}）的 source 重定向为 u（变成 u->x），数量/类型都保留。
  - 删除原先在 u 和 v 之间的内部边（ u->v )。
- 迭代合并：算法会循环扫描并反复合并，直到没有更多符合条件的节点可以合并（支持多级链式合并）。
"""

from typing import List, Set
from copy import deepcopy
from utils.cfg_structure import CFG, BlockNode, Edge

# 受保护边类型集合
DEFAULT_PROTECTED_EDGE_TYPES: Set[str] = {
    #"JUMPI", 
    "CALL", 
    "STATICCALL",
    "OTHERCALL",
    "RETURN",
    "REVERT",
    "DESTRUCT",
    "TERMINATE",
    "CREATE",
    "UNKNOWN"
}


def create_merged_cfg(cfg: CFG, protected_edge_types: Set[str] = None) -> CFG:
    """
    基于传入的 transaction-level CFG，按规则合并线性节点并返回同一 CFG（原地修改）。

    Args:
        cfg: 要处理的 CFG（utils.cfg_structure.CFG 对象）。
        protected_edge_types: 可选的受保护边类型集合（若为 None 则使用默认集合）。

    Returns:
        已合并后的同一个 CFG 对象（原地修改）。
    """
    if protected_edge_types is None:
        protected_edge_types = DEFAULT_PROTECTED_EDGE_TYPES

    def outgoing_edges_between(a: BlockNode, b: BlockNode) -> List[Edge]:
        return [e for e in cfg.edges if (e.source is a and e.target is b)]

    def incoming_edges_of(node: BlockNode) -> List[Edge]:
        return [e for e in cfg.edges if e.target is node]

    def remove_edge(edge: Edge) -> None:
        try:
            cfg.edges.remove(edge)
        except ValueError:
            pass

    def remove_node(node: BlockNode) -> None:
        try:
            cfg.nodes.remove(node)
        except ValueError:
            pass

    def is_protected_between(a: BlockNode, b: BlockNode) -> bool:
        # 如果 a->b 的任意一条边的类型属于受保护类型，则不可合并
        for e in outgoing_edges_between(a, b):
            if e.edge_type in protected_edge_types:
                return True
        return False

    merged_any = True
    max_iters = len(cfg.nodes)*10
    iter_count = 0

    # 反复扫描直到无法再合并
    while merged_any and iter_count < max_iters:
        iter_count += 1
        merged_any = False

        # 复制节点列表用于安全遍历（我们会在循环中修改 cfg.nodes / cfg.edges）
        nodes_snapshot = list(cfg.nodes)

        for u in nodes_snapshot:
            # 获取 u 的所有出边与去重后的子节点列表
            succ_edges = [e for e in cfg.edges if e.source is u]
            succ_target_ids = set()
            succ_targets = []
            for e in succ_edges:
                t = e.target
                if id(t) not in succ_target_ids:
                    succ_target_ids.add(id(t))
                    succ_targets.append(t)

            # 若u没有子节点，跳过
            if not succ_targets:
                continue

            # 若u有多个子节点，跳过
            if len(succ_targets) != 1:
                continue

            v = succ_targets[0]

            # 若 u->v 存在受保护边类型，跳过
            if is_protected_between(u, v):
                continue

            # 检查 v 的所有入边，确保它们全部来自 u（即没有其它节点指向 v）
            v_in_edges = incoming_edges_of(v)
            safe_to_merge = True
            for e in v_in_edges:
                if e.source is not u:
                    # 存在来自非 u 的入边，不可合并
                    safe_to_merge = False
                    break
            if not safe_to_merge:
                continue

            # 通过以上判断后，安全地将 v 合并到 u
            try:
                # 1) 合并指令序列（保持 u 在前，v 在后）
                u.instructions = list(u.instructions) + list(v.instructions)
            except Exception:
                # 若出现异常则跳过此对
                continue

            # 2) 更新 u 的终态信息（end_pc / terminator）
            u.end_pc = v.end_pc
            u.terminator = v.terminator

            # 3) 处理边：
            edges_snapshot = list(cfg.edges)
            for e in edges_snapshot:
                # v -> x (x 不是 u/v)  => 改为 u -> x
                if e.source is v and e.target is not u and e.target is not v:
                    e.source = u
                # u -> v (内部边) => 删除
                elif e.source is u and e.target is v:
                    remove_edge(e)

            # 4) 删除节点 v（所有与 v 相关的内部边已被处理）
            remove_node(v)

            merged_any = True
            break

    print(iter_count, "轮后处理，完成节点合并。")
    return cfg