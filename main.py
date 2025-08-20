import json
import os
from dotenv import load_dotenv
from utils.evm_information import TraceFormatter
from utils.basic_block import BasicBlockProcessor
from utils.cfg_transaction import CFGConstructor, render_transaction
from utils.cfg_contract import ContractCFGConnector, render_contract
from utils.cfg_static_complete import StaticCompleteCFGBuilder, render_static_complete

load_dotenv()

def create_result_directory(tx_hash: str) -> str:
    """创建结果目录结构: Result/交易哈希/"""
    # 移除交易哈希中的0x前缀作为目录名
    tx_dir_name = tx_hash.lstrip('0x')
    # 构建完整目录路径
    result_dir = os.path.join("Result", tx_dir_name)
    # 创建目录（如果不存在）
    os.makedirs(result_dir, exist_ok=True)
    return result_dir

def main():
    # 配置参数
    PROVIDER_URL = os.environ.get("GETH_API")
    TX_HASH = "0x476d0ae3e8229b7e85c6bf6103a4e4ab0d38e06fcce5dcc82aaeb2fb96bf21f2"

    try:
        # 创建结果目录
        result_dir = create_result_directory(TX_HASH)
        print(f"所有结果将保存到: {os.path.abspath(result_dir)}\n")

        # 初始化工具
        formatter = TraceFormatter(PROVIDER_URL)
        processor = BasicBlockProcessor()
        
        # 1. 获取交易的标准化trace
        print(f"正在获取交易 {TX_HASH} 的执行轨迹...")
        standardized_trace = formatter.get_standardized_trace(TX_HASH)
        print(f"成功获取轨迹，包含 {len(standardized_trace['steps'])} 个步骤\n")

        # 2. 提取涉及的合约地址
        contracts = formatter.extract_contracts_from_trace(standardized_trace)
        print(f"交易涉及 {len(contracts)} 个合约地址: {[addr[:8] + '...' for addr in contracts]}\n")

        # 3. 获取所有合约的字节码
        print("正在获取合约字节码...")
        contracts_bytecode = formatter.get_all_contracts_bytecode(TX_HASH)

        # 4. 转换字节码为基本块
        print("正在将字节码转换为基本块...")
        all_blocks = processor.process_multiple_contracts(contracts_bytecode)
        print(f"成功生成 {len(all_blocks)} 个基本块\n")

        # 5. 构建交易级控制流图(CFG)
        print("正在构建交易级控制流图...")
        cfg_constructor = CFGConstructor(all_blocks)
        tx_cfg = cfg_constructor.construct_cfg(standardized_trace)
        print(f"成功构建交易级CFG，包含 {len(tx_cfg.nodes)} 个节点和 {len(tx_cfg.edges)} 条边\n")

        # 6. 为每个合约构建独立的CFG
        print("正在构建合约级控制流图...")
        contract_cfgs = {}
        
        for contract_addr in contracts:
            contract_blocks = [b for b in all_blocks if b.address == contract_addr]
            if not contract_blocks:
                print(f"合约 {contract_addr[:8]}... 没有基本块，跳过...")
                continue
                
            contract_steps = [
                step for step in standardized_trace["steps"] 
                if step["address"] == contract_addr
            ]
            
            connector = ContractCFGConnector(contract_blocks)
            contract_cfg = connector.connect_contract_cfg(contract_steps)
            contract_cfgs[contract_addr] = contract_cfg
            
            print(f"合约 {contract_addr[:8]}... 的CFG构建完成，包含 {len(contract_cfg.nodes)} 个节点和 {len(contract_cfg.edges)} 条边")

        # 7. 为每个合约构建静态完整的CFG
        print("正在构建静态完整的合约级控制流图...")
        contract_cfgs_static = {}
        # 我们需要同时遍历 contracts_bytecode 列表，以获取原始字节码
        for contract_data in contracts_bytecode:
            contract_addr = contract_data["address"]
            contract_bytecode = contract_data["bytecode"] # 获取原始字节码
            contract_blocks = [b for b in all_blocks if b.address == contract_addr]
            if not contract_blocks:
                print(f"合约 {contract_addr[:8]}... 没有基本块，跳过...")
                continue
            # 现在需要传入 contract_bytecode 和 contract_blocks
            builder = StaticCompleteCFGBuilder(contract_bytecode, contract_blocks) # 修改：传递 contract_bytecode 和 contract_blocks
            static_cfg = builder.build_static_cfg()
            contract_cfgs_static[contract_addr] = static_cfg
            print(f"合约 {contract_addr[:8]}... 的静态CFG构建完成，包含 {len(static_cfg.nodes)} 个节点和 {len(static_cfg.edges)} 条边")

        # 8. 保存轨迹数据
        trace_path = os.path.join(result_dir, f"trace.json")
        with open(trace_path, "w") as f:
            json.dump(standardized_trace, f, indent=2)
        print(f"\n轨迹数据已保存到: {trace_path}")
        
        # 9. 保存基本块数据
        blocks_path = os.path.join(result_dir, f"blocks.json")
        with open(blocks_path, "w") as f:
            blocks_data = []
            for block in all_blocks:
                blocks_data.append({
                    "address": block.address,
                    "start_pc": block.start_pc,
                    "end_pc": block.end_pc,
                    "terminator": block.terminator,
                    "instructions": block.instructions
                })
            json.dump(blocks_data, f, indent=2)
        print(f"基本块数据已保存到: {blocks_path}")
        
        # 10. 保存交易级CFG的DOT文件
        tx_dot_path = os.path.join(result_dir, f"transaction_cfg.dot")
        render_transaction(tx_cfg, tx_dot_path)
        print(f"交易级CFG DOT文件已保存到: {tx_dot_path}")
        
        # 11. 保存每个合约的CFG DOT文件
        for addr, cfg in contract_cfgs.items():
            short_addr = addr.lstrip('0x')[:8]
            contract_dot_path = os.path.join(result_dir, f"contract_{short_addr}_cfg.dot")
            render_contract(cfg, contract_dot_path)
            print(f"合约 {short_addr} CFG DOT文件已保存到: {contract_dot_path}")
        # 12. 保存新的静态CFG DOT文件
        for addr, cfg in contract_cfgs_static.items():
            short_addr = addr.lstrip('0x')[:8]
            static_dot_path = os.path.join(result_dir, f"contract_{short_addr}_static_cfg.dot")
            render_static_complete(cfg, static_dot_path)
            print(f"合约 {short_addr} 静态CFG DOT文件已保存到: {static_dot_path}")

        print("\n===== 处理完成 =====")
        print(f"所有结果已保存到: {os.path.abspath(result_dir)}")
        
    except Exception as e:
        print(f"执行失败: {str(e)}")

if __name__ == "__main__":
    main()
