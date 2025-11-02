# evm_information.py 负责从节点上获取所有必要信息；
# 这些信息包括交易的标准化trace、涉及的contract address以及对应的bytecode；
# 包含对trace的结构定义；
# 包含获取每个step对应的contract address的逻辑；
# 不涉及其他对bytecode和trace的分析逻辑。

from typing import List, Dict, TypedDict, Set # 标准化数据结构定义
import logging # 标准化数据结构定义
import json
from web3 import Web3 # 导入Web3库用于与以太坊节点交互
import subprocess # 用于执行外部命令

logging.basicConfig(level=logging.INFO) # 设置日志级别为INFO
logger = logging.getLogger(__name__) # 创建日志记录器

# 标准化数据结构定义
class StandardizedStep(TypedDict): # 定义一个字典类型，包含以下字段
    address: str  # 0x开头的十六进制字符串
    pc: str       # 0x开头的十六进制字符串
    opcode: str   # 操作码名称
    stack: List[str]  # 0x开头的十六进制字符串

class StandardizedTrace(TypedDict): # 定义一个字典类型，包含以下字段
    tx_hash: str               # 0x开头的十六进制交易哈希
    steps: List[StandardizedStep]

class ContractBytecode(TypedDict):
    address: str  # 0x开头的十六进制字符串
    bytecode: str  # 0x开头的十六进制字符串

class TraceFormatter:
    def __init__(self, provider_url: str): # 初始化函数，接收一个以太坊节点的URL
        self.provider_url = provider_url
        self.web3 = Web3(Web3.HTTPProvider(provider_url)) # 创建Web3实例
        if not self.web3.is_connected(): # 检查是否连接成功
            raise ConnectionError("无法连接到以太坊节点，请检查provider URL是否正确")

    # 地址标准化（增加补0逻辑）
    def _normalize_address(self, address: str) -> str:
        """
        标准化以太坊地址格式，确保在0x后、数字前补0以满足42字符长度
        返回: 标准42字符地址(0x+40字符)或空字符串
        """
        if not address:
            return ""
            
        try:
            # 处理特殊格式：移除所有空白字符和多余前缀
            address_str = str(address).strip().lower().replace("0x0x", "0x")
            
            # 提取0x前缀和主体部分
            if address_str.startswith("0x"):
                prefix = "0x"
                body = address_str[2:]  # 去掉0x后的主体部分
            else:
                prefix = "0x"
                body = address_str  # 无0x前缀时直接取主体
            
            # 处理32字节地址（64字符）转20字节（40字符）
            if len(body) > 40:
                body = body[-40:]  # 取最后40个字符（标准地址长度）
            
            # 计算需要补充的0的数量（主体部分需40字符）
            if len(body) < 40:
                padding = "0" * (40 - len(body))
                body = padding + body  # 在主体前补0（即0x后面补0）
            
            # 组合完整地址
            full_address = f"{prefix}{body}"
            
            # 验证长度是否正确（0x + 40字符 = 42字符）
            if len(full_address) != 42:
                raise ValueError(f"地址长度异常: {len(full_address)}字符（预期42）")
            
            # 验证地址有效性并返回小写格式
            checksum_addr = Web3.to_checksum_address(full_address)
            return checksum_addr.lower()
            
        except Exception as e:
            logger.debug(f"地址标准化失败: {address} - {str(e)}")
            return ""

    # PC标准化
    def _normalize_pc(self, pc: int) -> str:
        return self.web3.to_hex(pc) # 使用Web3库将整数PC转换为十六进制字符串

    # 栈数据标准化
    def _normalize_stack(self, raw_stack: List[str]) -> List[str]:
        normalized = []
        for item in raw_stack or []: # 遍历原始栈数据
            if not item:
                # 空元素保留0x前缀的空值表示（而非64个0）
                normalized.append("0x")
                continue
            str_item = str(item) 
            # 确保0x前缀，不处理长度
            if str_item.startswith("0x"):
                normalized.append(str_item)
            else:
                normalized.append(f"0x{str_item}")
        return normalized

    # 获取交易初始目标地址
    def _get_initial_address(self, tx_hash: str) -> str:
        tx = self.web3.eth.get_transaction(tx_hash) # 使用Web3库获取指定交易哈希的交易信息
        return tx.get("to", "") # 获取交易的目标地址（合约或外部账户）

    # 获取并标准化trace,计算contract address（增加call是否成功判断）
    def get_standardized_trace(self, tx_hash: str) -> dict:
        """
        使用 foundry 的 cast run 获取并标准化交易执行轨迹
        """
        try:
            # === 使用 cast rpc 调用 debug_traceTransaction ===
            cmd = [
                "cast", "rpc",
                "debug_traceTransaction",
                tx_hash,
                '{"enableMemory":true,"disableStack":false,"disableStorage":false,"enableReturnData":true}',
                "-r", self.provider_url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            raw_trace = json.loads(result.stdout)

        except subprocess.CalledProcessError as e:
            logger.error(f"cast rpc 执行失败: {e.stderr}")
            raise RuntimeError(f"无法获取 trace：{tx_hash}") from e
        except json.JSONDecodeError as e:
            logger.error(f"解析 cast rpc 输出失败: {e}")
            raise RuntimeError(f"cast rpc 输出不是合法的 JSON: {tx_hash}") from e

        # === 从 Geth 的结果中提取 structLogs ===
        struct_logs = raw_trace.get("structLogs", [])
        steps = []

        # === 初始化地址栈 ===
        initial_address = self._normalize_address(self._get_initial_address(tx_hash))
        current_address = initial_address
        next_address = initial_address
        call_stack = [initial_address] if initial_address else []

        for i, step in enumerate(struct_logs):
            pc = step.get("pc", 0)
            opcode = step.get("op", "").upper()
            raw_stack = step.get("stack", [])

            logger.debug(f"步骤{i} | opcode: {opcode} | 当前地址: {current_address} | 栈长度: {len(raw_stack)}")


            # --- CALL 类指令 ---
            if opcode in {"CALL", "CALLCODE", "DELEGATECALL", "STATICCALL"}:
                if len(raw_stack) >= 7:
                    to_address_raw = raw_stack[-2]
                    to_address = self._normalize_address(to_address_raw)
                    is_valid_address = bool(to_address)

                    has_next_step = i < len(struct_logs) - 1
                    next_step_pc = None
                    if has_next_step:
                        next_step_pc = self._normalize_pc(struct_logs[i + 1].get("pc", 0))
                    is_next_pc_zero = has_next_step and next_step_pc == "0x0"

                    if is_valid_address and is_next_pc_zero:
                        call_stack.append(current_address)
                        next_address = to_address
                        logger.debug(f"地址切换: {current_address} -> {to_address}")
                    else:
                        if not is_valid_address:
                            logger.debug(f"无效目标地址: {to_address_raw} -> {to_address}")
                        if not is_next_pc_zero:
                            logger.debug(f"下一个步骤PC非0x0: {next_step_pc}")
                        next_address = current_address
                else:
                    logger.debug(f"CALL指令栈长度不足: {len(raw_stack)} < 7")
                    next_address = current_address

            # --- CREATE 类指令 ---
            elif opcode in ["CREATE", "CREATE2"]:
                new_address = ""
                if new_address:
                    new_address = self._normalize_address(new_address)
                    has_next_step = i < len(struct_logs) - 1
                    if has_next_step:
                        next_step_pc = self._normalize_pc(struct_logs[i + 1].get("pc", 0))
                        if next_step_pc == "0x0" and new_address:
                            call_stack.append(current_address)
                            next_address = new_address
                            logger.debug(f"创建新合约: {new_address}")
                        else:
                            next_address = current_address
                    else:
                        next_address = current_address
                else:
                    next_address = current_address

            # --- 终止指令 ---
            elif opcode in {"STOP", "RETURN", "REVERT", "INVALID", "SELFDESTRUCT"}:
                if len(call_stack) > 1:
                    next_address = call_stack.pop()
                    logger.debug(f"返回上一层地址: {next_address}")
                else:
                    next_address = current_address

            # --- 其他指令 ---
            else:
                next_address = current_address

            # 记录当前步骤
            steps.append({
                "address": current_address,
                "pc": self._normalize_pc(pc),
                "opcode": opcode,
                "stack": self._normalize_stack(raw_stack)
            })

            current_address = next_address

        return {
            "tx_hash": tx_hash,
            "steps": steps
        }

    # 提取合约地址
    def extract_contracts_from_trace(self, standardized_trace: StandardizedTrace) -> Set[str]:
        return {step["address"] for step in standardized_trace["steps"] if step["address"]}

    # 获取单个合约字节码
    def get_contract_bytecode(self, contract_address: str) -> ContractBytecode:
        normalized_addr = self._normalize_address(contract_address)
        if not normalized_addr or not self.web3.is_address(normalized_addr):
            raise ValueError(f"无效地址（需0x开头的十六进制）: {contract_address}")

        try:
            bytecode = self.web3.eth.get_code(Web3.to_checksum_address(normalized_addr))
            return {
                "address": normalized_addr,
                "bytecode": self.web3.to_hex(bytecode)
            }
        except Exception as e:
            logger.error(f"获取合约字节码失败: {e}")
            raise

    # 获取所有涉及的合约字节码
    def get_all_contracts_bytecode(self, tx_hash: str) -> List[ContractBytecode]:
        trace = self.get_standardized_trace(tx_hash)
        contracts = self.extract_contracts_from_trace(trace)

        return [self.get_contract_bytecode(addr) for addr in contracts if addr]
