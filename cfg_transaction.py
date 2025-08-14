# transaction.py负责构建整个transaction的CFG图
# 包含连接逻辑
# 包含Transaction Execution CFG渲染

from typing import List, Dict, Tuple, Optional, Set
from evm_information import StandardizedTrace, StandardizedStep
from basic_block import Block, BasicBlockProcessor
from cfg_structure import CFG, BlockNode, Edge


class CFGConstructor:
    def __init__(self, all_base_blocks: List[Block]):
        # 基础块索引：(address, start_pc) -> 基础块（包含完整指令列表）
        self.base_block_map: Dict[Tuple[str, str], Block] = {} # 
        for block in all_base_blocks:
            key = (block.address, block.start_pc)
            self.base_block_map[key] = block
    # 将所有基础块存储在字典中，便于快速查找
        # 分块触发的 opcode（与 basic_block.py 保持一致）
        self.split_opcodes = {
            "JUMP", "JUMPI", "CALL", "CALLCODE", "DELEGATECALL", "STATICCALL",
            "CREATE", "CREATE2", "STOP", "RETURN", "REVERT", "INVALID", "SELFDESTRUCT"
        }

    def _find_base_block(self, address: str, pc: str) -> Block:
        """通过 address 和 start_pc 查找基础块（确保返回包含完整指令的块）"""
        key = (address, pc)
        if key in self.base_block_map:
            return self.base_block_map[key]
        raise ValueError(f"未找到 address={address} 且 start_pc={pc} 的基础块")

    def construct_cfg(self, trace: StandardizedTrace) -> CFG:
        cfg = CFG(tx_hash=trace["tx_hash"])
        steps = trace["steps"]
        if not steps:
            return cfg

        processed_nodes: Dict[Tuple[str, str], BlockNode] = {}  # 复用节点
        current_step_idx = 0  # 当前处理的 step 索引

        # 处理第一个块
        first_step = steps[current_step_idx]
        try:
            current_base_block = self._find_base_block(
                address=first_step["address"],
                pc=first_step["pc"]
            )
        except ValueError as e:
            raise RuntimeError(f"初始化第一个块失败：{e}")

        # 创建第一个节点（自动包含完整指令列表）
        current_node_key = (current_base_block.address, current_base_block.start_pc)
        current_node = BlockNode(current_base_block)
        processed_nodes[current_node_key] = current_node
        cfg.add_node(current_node)

        # 遍历 trace，按块处理
        while current_step_idx < len(steps):
            current_step = steps[current_step_idx]
            current_opcode = current_step["opcode"]

            # 遇到分块触发指令时，切换到下一个块
            if current_opcode in self.split_opcodes:
                if current_step_idx + 1 >= len(steps):
                    break  # 已到 trace 末尾

                next_step = steps[current_step_idx + 1]
                try:
                    next_base_block = self._find_base_block(
                        address=next_step["address"],
                        pc=next_step["pc"]
                    )
                except ValueError as e:
                    print(f"警告：步骤 {current_step_idx + 1} 对应的下一个块未找到：{e}")
                    current_step_idx += 1
                    continue

                # 复用或创建下一个节点（包含完整指令列表）
                next_node_key = (next_base_block.address, next_base_block.start_pc)
                if next_node_key in processed_nodes:
                    next_node = processed_nodes[next_node_key]
                else:
                    next_node = BlockNode(next_base_block)  # 指令列表自动包含
                    processed_nodes[next_node_key] = next_node
                    cfg.add_node(next_node)

                # 创建边
                edge_type = self._get_edge_type(current_opcode) # 根据当前指令确定边类型
                cfg.add_edge(
                    source=current_node,
                    target=next_node,
                    edge_type=edge_type
                )

                current_node = next_node

            current_step_idx += 1

        return cfg

    def _get_edge_type(self, opcode: str) -> str:
        """根据终止 opcode 确定边类型"""
        if opcode in {"JUMP", "JUMPI"}:
            return "JUMP"
        elif opcode in {"CALL", "CALLCODE", "DELEGATECALL", "STATICCALL"}:
            return "CALL"
        elif opcode in {"RETURN", "REVERT"}:
            return "RETURN"
        elif opcode == "SELFDESTRUCT":
            return "DESTRUCT"
        elif opcode in {"STOP", "INVALID"}:
            return "TERMINATE"
        elif opcode in {"CREATE", "CREATE2"}:
            return "CREATE"
        else:
            return "UNKNOWN"
# 上面这段代码定义了一个名为`CFGConstructor`的类，它用于构建交易的控制流图（CFG）。这个类有一个构造函数`__init__`，它接收一个包含所有基础块的列表，并将这些块存储在一个字典中，以便快速查找。
# 这个类还有一个`construct_cfg`方法，它接收一个标准化的交易跟踪（trace），并构建对应的CFG图。它会遍历交易的每个步骤，处理分块触发指令，并创建节点和边。
# 该类还包含一个私有方法`_get_edge_type`，用于根据终止指令确定边的类型。

# 渲染CFG为DOT文件（显示所有指令并按合约染色）
def render_transaction(cfg: CFG, output_path: str, rankdir: str = "TB") -> None: 
    """
    将CFG渲染为DOT文件，显示所有指令，并为不同合约的块自动分配不同颜色
    """
    # 定义一组协调的颜色用于不同合约（可以根据需要扩展）
    contract_colors = [
    "#FF9E9E",  "#81C784",  "#64B5F6",  "#FFF176",  "#BA68C8",  
    "#4DD0E1",  "#FFB74D",  "#F48FB1",  "#AED581",  "#7986CB", 
    "#FF8A65",  "#4DB6AC", "#DCE775",   "#9575CD",  "#FFD54F" 
    ]
    
    # 获取所有唯一的合约地址并分配颜色
    unique_addresses: Set[str] = {node.address for node in cfg.nodes}
    address_color_map: Dict[str, str] = {}
    
    for i, address in enumerate(unique_addresses):
        color_index = i % len(contract_colors) # 取余数，循环使用颜色
        address_color_map[address] = contract_colors[color_index]
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('digraph CFG {\n')
        f.write(f'    rankdir={rankdir};\n') # 指定图的布局方向
        # 调整节点样式以适应可能较长的指令列表
        f.write('    node [shape=box, style="filled, rounded", '
                'fontname="Arial", fontsize=8, margin=0.1];\n')
        f.write('    edge [fontname="Arial", fontsize=8, color="#555555"];\n\n')
        
        # 写入所有节点（包含所有指令和合约特定颜色）
        for node in cfg.nodes:
            node_id = f"node_{node.address.replace('0x', '')}_{node.start_pc.replace('0x', '')}"
            # 获取该节点的颜色
            node_color = address_color_map.get(node.address, "#e0e0e0")  # 默认灰色
            
            # 节点标签包含地址、PC和所有指令
            node_label = (f"{node.address[:8]}...\n"
                         f"start: {node.start_pc} | end: {node.end_pc}\n"
                         f"terminator: {node.terminator}\n"
                         f"---------\n"
                         f"{node.get_instructions_str()}")
            # 替换引号避免DOT语法错误
            node_label = node_label.replace('"', '\\"')
            f.write(f'    "{node_id}" [label="{node_label}", fillcolor="{node_color}"];\n')
        
        f.write('\n')
        
        # 写入所有边
        for edge in cfg.edges:
            source_id = f"node_{edge.source.address.replace('0x', '')}_{edge.source.start_pc.replace('0x', '')}"
            target_id = f"node_{edge.target.address.replace('0x', '')}_{edge.target.start_pc.replace('0x', '')}"
            edge_label = f"id: {edge.edge_id} ({edge.edge_type})"
            f.write(f'    "{source_id}" -> "{target_id}" [label="{edge_label}"];\n')
        
        f.write('}')

    print(f"CFG已渲染为DOT文件：{output_path}")
