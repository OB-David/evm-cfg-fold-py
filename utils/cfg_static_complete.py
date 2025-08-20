# cfg_static_complete.py
# 负责为交易中涉及的每个合约构建完整的、静态的控制流图（CFG）
# 该图基于合约的完整字节码和EVM语义，通过栈值分析精确连接跳转指令
# 与 cfg_contract.py (动态路径) 和 cfg_transaction.py (跨合约流) 保持独立

from typing import List, Dict, Tuple, Optional
from basic_block import Block
from cfg_structure import CFG, BlockNode, Edge
from web3 import Web3
import logging
import pyevmasm as evmasm  # 需要使用 pyevmasm 进行反汇编以获取操作数
import re

logger = logging.getLogger(__name__)

BASIC_BLOCK_END = [
    "STOP",
    "SELFDESTRUCT",
    "RETURN",
    "REVERT",
    "INVALID",
    "SUICIDE",
    "JUMP",
    "JUMPI",
] # 这些指令会导致基本块结束

class SimpleStackValueAnalyzer:
    """
    一个简化的栈值分析器，用于推断 JUMP 和 JUMPI 指令的跳转目标。
    它模拟栈操作，处理 PUSH, DUP, SWAP, ADD, SUB, MUL, DIV 指令。
    """
    def __init__(self, bytecode: str):
        self.bytecode = bytecode
        self.instructions = list(evmasm.disassemble_all(bytes.fromhex(bytecode[2:])))
        self.instr_by_pc = {instr.pc: instr for instr in self.instructions} # 将指令按PC索引

    def get_jump_target(self, jump_pc: int) -> Optional[int]:
        """
        尝试获取在指定PC处的 JUMP 或 JUMPI 指令的跳转目标。
        
        Args:
            jump_pc (int): JUMP 或 JUMPI 指令的PC。
        
        Returns:
            Optional[int]: 跳转目标的PC，如果无法确定则返回 None。
        """
        jump_instr = self.instr_by_pc.get(jump_pc)
        if not jump_instr or jump_instr.name not in ["JUMP", "JUMPI"]:
            return None
        
        stack = []
        # 模拟执行，直到遇到 JUMP/JUMPI 指令
        for instr in reversed(self.instructions[:self.instructions.index(jump_instr)]):
            if instr.name.startswith("PUSH"):
                stack.append(instr.operand)
            elif instr.name.startswith("DUP"):
                n = int(instr.name[3:])
                if len(stack) >= n:
                    stack.append(stack[n - 1])
                else:
                    return None  # 栈不足
            elif instr.name.startswith("SWAP"):
                n = int(instr.name[4:])
                if len(stack) >= n + 1:
                    stack[-1], stack[-n - 1] = stack[-n - 1], stack[-1]
                else:
                    return None  # 栈不足
            elif instr.name in ["POP"]:
                if stack:
                    stack.pop()
                else:
                    return None
            # --- 添加对 ADD, SUB, MUL, DIV 指令的处理 ---
            elif instr.name in ["ADD", "SUB", "MUL", "DIV"]:
                if len(stack) >= 2:
                    # 取出栈顶的两个元素
                    val1 = stack.pop()
                    val2 = stack.pop()

                    # 尝试将它们转换为整数
                    try:
                        num1 = int(val1)
                        num2 = int(val2)
                    except ValueError:
                        # 如果无法转换为整数，则无法进行计算，停止分析
                        return None

                    # 根据指令进行相应的计算
                    if instr.name == "ADD":
                        result = num2 + num1  # 注意顺序：num2 在栈底，num1 在栈顶
                    elif instr.name == "SUB":
                        result = num2 - num1
                    elif instr.name == "MUL":
                        result = num2 * num1
                    elif instr.name == "DIV":
                        if num1 == 0:
                            # 避免除以零
                            result = 0
                        else:
                            result = num2 // num1  # 使用整数除法

                    # 将结果压入栈
                    stack.append(result)
                else:
                    return None  # 栈不足
            else:
                # 遇到其他指令，停止分析
                break

        # 栈顶元素即为跳转目标
        if stack:
            return stack[-1]
        else:
            return None

class StaticCompleteCFGBuilder:
    """
    为单个合约构建静态完整控制流图的构建器。
    它利用已有的基本块划分，并结合栈值分析来精确建立控制流连接。
    """
    
    def __init__(self, contract_bytecode: str, contract_blocks: List[Block]):
        """
        初始化构建器。
        
        Args:
            contract_bytecode (str): 该合约的原始字节码（0x开头）。
            contract_blocks (List[Block]): 该合约的基本块列表。
        """
        self.contract_bytecode = contract_bytecode
        self.contract_blocks = contract_blocks
        # self.instructions = list(evmasm.disassemble_all(bytes.fromhex(contract_bytecode[2:]))) # 移除
        # if not self.instructions:
        #     raise ValueError("contract_bytecode 不能为空")
        
        # 所有基本块都属于同一个合约，优先用第一个块的地址作为合约地址，否则用字节码前20字节作为地址
        self.contract_address = contract_blocks[0].address if contract_blocks else "0x"+"".join(re.findall(r"[0-9a-f]{2}", contract_bytecode[2:])[:20]) 
        
        # 建立关键索引：
        # (address, start_pc) -> Block
        self.block_by_start_pc: Dict[Tuple[str, str], Block] = {
            (block.address, block.start_pc): block for block in self.contract_blocks
        }
        # (address, pc) -> start_pc (用于根据任意PC定位其所属块)
        self.pc_to_start_pc: Dict[Tuple[str, str], str] = {}
        for block in self.contract_blocks:
            for (pc, _) in block.instructions:
                self.pc_to_start_pc[(block.address, pc)] = block.start_pc

        # 初始化栈值分析器
        self.sva = SimpleStackValueAnalyzer(contract_bytecode)

    def _find_block_by_start_pc(self, address: str, start_pc: str) -> Block:
        """通过地址和起始PC查找基本块。"""
        key = (address, start_pc)
        block = self.block_by_start_pc.get(key)
        if block is None:
            raise ValueError(f"未找到地址 {address} 起始PC为 {start_pc} 的基本块")
        return block

    def _find_block_by_pc(self, address: str, pc: str) -> Block:
        """通过地址和任意PC查找其所属的基本块。"""
        try:
            start_pc = self.pc_to_start_pc[(address, pc)]
            return self._find_block_by_start_pc(address, start_pc)
        except KeyError:
            raise ValueError(f"PC {pc} 不属于合约 {address} 的任何基本块")

    def _get_edge_type(self, opcode: str) -> str:
        """根据终止指令确定边的类型。"""
        edge_type_map = {
            "JUMP": "JUMP",
            "JUMPI": "JUMPI",
            "CALL": "CALL",
            "CALLCODE": "CALL",
            "DELEGATECALL": "DELEGATECALL",
            "STATICCALL": "STATICCALL",
            "RETURN": "RETURN",
            "REVERT": "REVERT",
            "SELFDESTRUCT": "DESTRUCT",
            "STOP": "TERMINATE",
            "INVALID": "INVALID",
            "CREATE": "CREATE",
            "CREATE2": "CREATE",
            "SEQUENCE": "SEQUENCE",  # 添加
            "CONDITION_TRUE": "CONDITION_TRUE", # 添加
            "CONDITION_FALSE": "CONDITION_FALSE" # 添加
        }
        return edge_type_map.get(opcode, "UNKNOWN")

    def _get_opcode_length(self, opcode: str) -> int:
        """获取EVM指令的长度（字节数）。"""
        if opcode.startswith("PUSH"):
            try:
                n = int(opcode[4:])
                return 1 + n
            except ValueError:
                return 1
        return 1
    # 上面的函数用于获取EVM指令的长度，如果PUSH后面跟的是数字，则长度为1+数字，否则为1。
    def _connect_blocks(self, cfg: CFG, current_node: BlockNode, next_node: BlockNode, edge_type: str):
        """连接两个基本块"""
        edge = Edge(edge_id=cfg._next_edge_id, source=current_node, target=next_node, edge_type=edge_type)
        cfg.edges.append(edge)
        cfg._next_edge_id += 1

    def build_static_cfg(self) -> CFG:
        """
        构建合约的静态完整控制流图。
        
        核心逻辑：
        1. 将所有基本块作为节点添加到图中。
        2. 遍历每个基本块，根据其终止指令和EVM规则，建立到其他块的边。
        3. 对于 JUMP 和 JUMPI，使用 SimpleStackValueAnalyzer 精确推断跳转目标。
        4. 对于非跳转/终止指令，连接到下一个PC所在的块。
        
        Returns:
            CFG: 构建完成的静态控制流图。
        """
        # 初始化CFG
        cfg = CFG(tx_hash=f"static_complete_{self.contract_address}")
        
        # 创建并添加所有节点
        node_map: Dict[Tuple[str, str], BlockNode] = {}  # (address, start_pc) -> BlockNode
        for block in self.contract_blocks:
            node = BlockNode(block)
            node_key = (node.address, node.start_pc)
            node_map[node_key] = node
            cfg.add_node(node)

        # 为每个节点建立出边
        for block in self.contract_blocks:
            node_key = (block.address, block.start_pc)
            current_node = node_map[node_key]
            last_instruction = block.instructions[-1] if block.instructions else None
            
            if not last_instruction:
                continue
            
            pc, terminator_opcode = last_instruction[0], last_instruction[1]
            pc_int = int(pc, 16)  # 转换为整数用于计算

            # --- 规则1: JUMPI 指令 ---
            if terminator_opcode == "JUMPI":
                # 后继1: 下一条指令 (fall-through) - CONDITION_FALSE
                next_pc_int = pc_int + self._get_opcode_length("JUMPI")
                next_pc_hex = Web3.to_hex(next_pc_int)
                try:
                    fallthrough_block = self._find_block_by_pc(block.address, next_pc_hex)
                    fallthrough_key = (fallthrough_block.address, fallthrough_block.start_pc)
                    if fallthrough_key in node_map:
                        fallthrough_node = node_map[fallthrough_key]
                        edge_type = self._get_edge_type("CONDITION_FALSE") # CONDITION_FALSE
                        self._connect_blocks(cfg, current_node, fallthrough_node, edge_type)
                except ValueError:
                    pass  # 目标PC可能无效

                # 后继2: 跳转目标 (jump target) - CONDITION_TRUE
                # 使用栈值分析器获取目标PC
                target_pc_int = self.sva.get_jump_target(pc_int)
                if target_pc_int is not None:
                    target_pc_hex = Web3.to_hex(target_pc_int)
                    try:
                        target_block = self._find_block_by_pc(block.address, target_pc_hex)
                        target_key = (target_block.address, target_block.start_pc)
                        if target_key in node_map:
                            target_node = node_map[target_key]
                            # 避免自环
                            if target_node != current_node:
                                edge_type = self._get_edge_type("CONDITION_TRUE") # CONDITION_TRUE
                                self._connect_blocks(cfg, current_node, target_node, edge_type)
                    except ValueError:
                        # 目标PC可能不是一个有效的JUMPDEST块
                        pass
            # 如果是JUMPI命令，一条边连接到下一条指令，另一条边连接到跳转目标。
            # --- 规则 2: JUMP 指令 ---
            elif terminator_opcode == "JUMP":
                # 使用栈值分析器获取目标PC
                target_pc_int = self.sva.get_jump_target(pc_int)
                if target_pc_int is not None:
                    target_pc_hex = Web3.to_hex(target_pc_int)
                    try:
                        target_block = self._find_block_by_pc(block.address, target_pc_hex)
                        target_key = (target_block.address, target_block.start_pc)
                        if target_key in node_map:
                            target_node = node_map[target_key]
                            if target_node != current_node:
                                edge_type = self._get_edge_type("JUMP")
                                self._connect_blocks(cfg, current_node, target_node, edge_type)
                    except ValueError:
                        pass

            # --- 规则 3: 终止指令 ---
            # STOP, RETURN, REVERT, INVALID, SELFDESTRUCT 没有后继
            elif terminator_opcode in {"STOP", "RETURN", "REVERT", "INVALID", "SELFDESTRUCT"}:
                continue

            # --- 规则 4: 调用和创建指令 ---
            # CALL, CALLCODE, DELEGATECALL, STATICCALL, CREATE, CREATE2
            # 执行后，控制流会返回到下一条指令
            elif terminator_opcode in {"CALL", "CALLCODE", "DELEGATECALL", "STATICCALL", 
                                     "CREATE", "CREATE2"}:
                next_pc_int = pc_int + self._get_opcode_length(terminator_opcode)
                next_pc_hex = Web3.to_hex(next_pc_int)
                try:
                    next_block = self._find_block_by_pc(block.address, next_pc_hex)
                    next_key = (next_block.address, next_block.start_pc)
                    if next_key in node_map:
                        next_node = node_map[next_key]
                        edge_type = self._get_edge_type(terminator_opcode)
                        self._connect_blocks(cfg, current_node, next_node, edge_type)
                except ValueError:
                    pass

            # --- 规则 5: 顺序执行流 (修正版) ---
            # **核心改进**：只要不是上面处理过的指令（JUMP, JUMPI, 终止指令, 调用指令），
            # 都应该有顺序流。这包括了 ADD, MUL, PUSH, POP 等所有普通指令。
            # 这是连接由 JUMPDEST 分割产生的块的关键。
            else:
                # 计算下一条指令的PC
                next_pc_int = pc_int + self._get_opcode_length(terminator_opcode)
                next_pc_hex = Web3.to_hex(next_pc_int)
                try:
                    # 尝试找到下一个PC所在的块
                    next_block = self._find_block_by_pc(block.address, next_pc_hex)
                    next_key = (next_block.address, next_block.start_pc)
                    if next_key in node_map:
                        next_node = node_map[next_key]
                        edge_type = self._get_edge_type("SEQUENCE")
                        self._connect_blocks(cfg, current_node, next_node, edge_type)
                except ValueError:
                    # 下一个PC可能不属于任何块（例如在合约末尾），忽略
                    pass
        self.remove_unreachable_instruction_blocks(cfg, node_map)
        return cfg
    
    def remove_unreachable_instruction_blocks(self, cfg: CFG, node_map: Dict[Tuple[str, str], BlockNode]) -> None:
        """移除没有入边的基本块（除了入口点和 JUMPDEST 开头的块）"""
        to_remove: List[BlockNode] = []
        for node in cfg.nodes:
            # 检查是否有入边
            has_incoming_edge = False
            for edge in cfg.edges:
                if edge.target == node:
                    has_incoming_edge = True
                    break

            # 如果没有入边，并且不是入口点或 JUMPDEST 开头的块，则添加到待移除列表
            if not has_incoming_edge and node.base_block.start_pc != "0x0":
                # 获取基本块的第一条指令
                first_instruction = node.base_block.instructions[0] if node.base_block.instructions else None
                if first_instruction is not None and first_instruction[1] != "JUMPDEST":
                    to_remove.append(node)

        # 移除基本块
        for node in to_remove:
            cfg.remove_node(node)
            del node_map[(node.address, node.start_pc)]

def render_static_complete(cfg: CFG, output_path: str, rankdir: str = "TB") -> None:
    """
    将静态完整CFG渲染为DOT文件。
    
    Args:
        cfg (CFG): 要渲染的静态完整控制流图。
        output_path (str): 输出文件路径。
        rankdir (str): 布局方向 (TB: 从上到下, LR: 从左到右)。
    """
    edge_color_map = {
        "JUMP": "#ff9800",
        "JUMPI": "#ff9800",
        "CALL": "#4caf50",
        "RETURN": "#2196f3",
        "DESTRUCT": "#f44336",
        "TERMINATE": "#9e9e9e",
        "CREATE": "#8bc34a",
        "UNKNOWN": "#bdbdbd",
        "CONDITION_TRUE": "#9ece6a",
        "CONDITION_FALSE": "#f7768e"
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('digraph Static_Complete_CFG {\n')
        f.write(f'    rankdir={rankdir};\n')
        f.write('    node [shape=box, style="filled, rounded", '
                'fontname="Monospace", fontsize=9, margin=0.15];\n')
        f.write('    edge [fontname="Arial", fontsize=8, penwidth=1.2];\n')
        # 前面的设置定义了节点和边的样式
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
    
    print(f"静态完整CFG已渲染至: {output_path}（布局方向: {rankdir}）")