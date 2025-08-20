# cfg_contract.py负责构建多个contract的CFG图
# 包含连接逻辑
# 包含Transaction Execution CFG渲染

from typing import List, Dict, Tuple, Optional, Set
from evm_information import StandardizedStep
from basic_block import Block
from cfg_structure import CFG, BlockNode, Edge


class ContractCFGConnector:
    """合约内部CFG连接处理器（与transaction代码风格完全一致）"""
    def __init__(self, contract_blocks: List[Block]):
        self.contract_blocks = contract_blocks
        self.contract_address = contract_blocks[0].address if contract_blocks else ""
        
        # 基础块索引：(address, start_pc) -> 基础块
        self.base_block_map: Dict[Tuple[str, str], Block] = {}
        for block in contract_blocks:
            key = (block.address, block.start_pc)
            self.base_block_map[key] = block

        # PC到起始PC的映射（用于精确匹配）
        self.pc_to_start_pc: Dict[Tuple[str, str], str] = {}
        for block in contract_blocks:
            for (pc, _) in block.instructions:
                key = (block.address, pc)
                self.pc_to_start_pc[key] = block.start_pc

        # 触发广义跳转的 opcode（与 basic_block.py 保持一致）
        self.split_opcodes = {
            "JUMP", "JUMPI", "CALL", "CALLCODE", "DELEGATECALL", "STATICCALL",
            "CREATE", "CREATE2", "STOP", "RETURN", "REVERT", "INVALID", "SELFDESTRUCT"
        }
        
        # 跳转相关的opcode
        self.jump_opcodes = {"JUMP", "JUMPI"}

    def _find_base_block(self, address: str, start_pc: str) -> Block:
        """通过 address 和 start_pc 查找基础块"""
        key = (address, start_pc)
        if key in self.base_block_map:
            return self.base_block_map[key]
        raise ValueError(f"未找到 address={address} 且 start_pc={start_pc} 的基础块")

    def connect_contract_cfg(self, contract_steps: List[StandardizedStep]) -> CFG:
        """构建合约内部CFG（完善版，包含JUMPDEST处理逻辑）"""
        cfg = CFG(tx_hash=f"contract_{self.contract_address}")
        if not self.contract_blocks or not contract_steps:
            return cfg

        processed_nodes: Dict[Tuple[str, str], BlockNode] = {}  # 复用节点
        current_step_idx = 0  # 当前处理的步骤索引

        # 处理第一个块
        first_step = contract_steps[current_step_idx]
        try:
            pc_key = (first_step["address"], first_step["pc"])
            start_pc = self.pc_to_start_pc[pc_key]
            current_base_block = self._find_base_block(first_step["address"], start_pc)
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"初始化第一个块失败：{e}")

        # 创建第一个节点
        current_node_key = (current_base_block.address, current_base_block.start_pc)
        current_node = BlockNode(current_base_block)
        processed_nodes[current_node_key] = current_node
        cfg.add_node(current_node)

        # 遍历步骤，按块实时处理
        while current_step_idx < len(contract_steps):
            current_step = contract_steps[current_step_idx]
            current_opcode = current_step["opcode"]

            # 处理JUMPDEST：检查上一步是否为JUMP/JUMPI
            if current_opcode == "JUMPDEST":
                # 确保不是第一步，有上一个step可以检查
                if current_step_idx > 0:
                    prev_step = contract_steps[current_step_idx - 1]
                    prev_opcode = prev_step["opcode"]
                    
                    # 如果上一步不是JUMP/JUMPI，需要添加连接
                    if prev_opcode not in self.jump_opcodes:
                        # 查找上一步所在块（通过pc映射找到起始pc）
                        try:
                            prev_pc_key = (prev_step["address"], prev_step["pc"])
                            prev_start_pc = self.pc_to_start_pc[prev_pc_key]
                            prev_block = self._find_base_block(prev_step["address"], prev_start_pc)
                        except (KeyError, ValueError) as e:
                            print(f"警告：上一步对应的块未找到：{e}")
                            current_step_idx += 1
                            continue
                        
                        # 查找当前JUMPDEST所在的块
                        try:
                            current_jumpdest_pc_key = (current_step["address"], current_step["pc"])
                            jumpdest_start_pc = self.pc_to_start_pc[current_jumpdest_pc_key]
                            current_jumpdest_block = self._find_base_block(
                                current_step["address"], 
                                jumpdest_start_pc
                            )
                        except (KeyError, ValueError) as e:
                            print(f"警告：JUMPDEST对应的块未找到：{e}")
                            current_step_idx += 1
                            continue
                        
                        # 获取或创建前后节点
                        prev_node_key = (prev_block.address, prev_block.start_pc)
                        if prev_node_key in processed_nodes:
                            prev_node = processed_nodes[prev_node_key]
                        else:
                            prev_node = BlockNode(prev_block)
                            processed_nodes[prev_node_key] = prev_node
                            cfg.add_node(prev_node)
                        
                        current_jumpdest_node_key = (current_jumpdest_block.address, current_jumpdest_block.start_pc)
                        if current_jumpdest_node_key in processed_nodes:
                            jumpdest_node = processed_nodes[current_jumpdest_node_key]
                        else:
                            jumpdest_node = BlockNode(current_jumpdest_block)
                            processed_nodes[current_jumpdest_node_key] = jumpdest_node
                            cfg.add_node(jumpdest_node)
                        
                        # 创建非跳转引起的连接边
                        cfg.add_edge(
                            source=prev_node,
                            target=jumpdest_node,
                            edge_type="NOTJUMP"  # 标记为非跳转引起的连接
                        )
                        print(f"添加JUMPDEST连接边：从 {prev_node.start_pc} 到 {jumpdest_node.start_pc}")
                        
                        # 更新当前节点为JUMPDEST所在的节点，保持控制流连续性
                        current_node = jumpdest_node

            # 遇到分块触发指令时，切换到下一个块
            if current_opcode in self.split_opcodes:
                if current_step_idx + 1 >= len(contract_steps):
                    break  # 已到步骤末尾

                next_step = contract_steps[current_step_idx + 1]
                try:
                    next_pc_key = (next_step["address"], next_step["pc"])
                    next_start_pc = self.pc_to_start_pc[next_pc_key]
                    next_base_block = self._find_base_block(next_step["address"], next_start_pc)
                except (KeyError, ValueError) as e:
                    print(f"警告：步骤 {current_step_idx + 1} 对应的下一个块未找到：{e}")
                    current_step_idx += 1
                    continue

                # 复用或创建下一个节点
                next_node_key = (next_base_block.address, next_base_block.start_pc)
                if next_node_key in processed_nodes:
                    next_node = processed_nodes[next_node_key]
                else:
                    next_node = BlockNode(next_base_block)
                    processed_nodes[next_node_key] = next_node
                    cfg.add_node(next_node)

                # 创建边
                edge_type = self._get_edge_type(current_opcode)
                cfg.add_edge(
                    source=current_node,
                    target=next_node,
                    edge_type=edge_type
                )

                # 更新当前节点和块
                current_node = next_node
                current_base_block = next_base_block

            current_step_idx += 1

        return cfg

    def _get_edge_type(self, opcode: str) -> str:
        """根据终止 opcode 确定边类型"""
        if opcode in {"JUMP"}:
            return "JUMP"
        elif opcode in {"JUMPI"}:
            return "JUMPI"  # 与交易级保持一致，区分JUMP和JUMPI
        elif opcode in {"CALL", "STATICCALL"}:
            return "CALL"
        elif opcode in {"CALLCODE", "DELEGATECALL"}:
            return "OTHERCALL"  # 细分调用类型
        elif opcode in {"RETURN"}:
            return "RETURN"
        elif opcode in {"REVERT"}:
            return "REVERT"
        elif opcode == "SELFDESTRUCT":
            return "DESTRUCT"
        elif opcode in {"STOP", "INVALID"}:
            return "TERMINATE"
        elif opcode in {"CREATE", "CREATE2"}:
            return "CREATE"
        else:
            return "UNKNOWN"



def render_contract(cfg: CFG, output_path: str, rankdir: str = "TB") -> None:
    """
    将合约CFG渲染为DOT文件（默认从上到下排布）
    
    参数:
        cfg: 要渲染的合约控制流图
        output_path: 输出文件路径
        rankdir: 布局方向 (TB: 从上到下, LR: 从左到右)，默认TB
    """
    edge_color_map = {
        "JUMP": "#ff9800",
        "CALL": "#4caf50",
        "RETURN": "#2196f3",
        "DESTRUCT": "#f44336",
        "TERMINATE": "#9e9e9e",
        "CREATE": "#8bc34a",
        "UNKNOWN": "#bdbdbd"
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('digraph Contract_CFG {\n')
        f.write(f'    rankdir={rankdir};\n')  # 核心修改：默认从上到下布局
        f.write('    node [shape=box, style="filled, rounded", '
                'fontname="Monospace", fontsize=9, margin=0.15];\n')
        f.write('    edge [fontname="Arial", fontsize=8, penwidth=1.2];\n\n')
        
        # 写入节点
        for node in cfg.nodes:
            node_id = f"block_{node.start_pc.replace('0x', '')}"
            instr_lines = [f"{pc}: {opcode}" for (pc, opcode) in node.instructions]
            instr_str = "\n".join(instr_lines)
            
            node_label = (f"合约: {node.address[:8]}...\n"
                         f"起始PC: {node.start_pc}\n"
                         f"终止PC: {node.end_pc}\n"
                         f"终止指令: {node.terminator}\n"
                         f"---------\n"
                         f"{instr_str}")
            node_label = node_label.replace('"', '\\"')
            f.write(f'    "{node_id}" [label="{node_label}", fillcolor="#e6f7ff"];\n')
        
        f.write('\n')
        
        # 写入边
        for edge in cfg.edges:
            source_id = f"block_{edge.source.start_pc.replace('0x', '')}"
            target_id = f"block_{edge.target.start_pc.replace('0x', '')}"
            edge_color = edge_color_map.get(edge.edge_type, "#bdbdbd")
            edge_label = f"#{edge.edge_id} ({edge.edge_type})"
            f.write(f'    "{source_id}" -> "{target_id}" [label="{edge_label}", '
                    f'color="{edge_color}"];\n')
        
        f.write('}')
    
    print(f"合约CFG已渲染至: {output_path}（布局方向: {rankdir}）")
