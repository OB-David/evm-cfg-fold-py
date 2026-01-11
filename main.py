import json
import os
from dotenv import load_dotenv
from utils.evm_information import TraceFormatter
from utils.basic_block import BasicBlockProcessor
from utils.cfg_transaction import CFGConstructor, render_transaction
from utils.fold_node import create_merged_cfg
from utils.node_head_only import strip_instructions_from_dot

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
    TX_HASH = "0x091078ab83662beb848b8d5281185b3b3efffb9d58eca9a8203d734fccbf3c5d"

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
        

        # 2. 提取涉及的合约地址
        contracts = formatter.extract_contracts_from_trace(standardized_trace)
        print(f"交易涉及 {len(contracts)} 个合约地址: {[addr[:8] + '...' for addr in contracts]}\n")

        # 3. 获取所有合约的字节码
        print("正在获取合约字节码...")
        contracts_bytecode = formatter.get_all_contracts_bytecode(all_contracts = contracts )

        # 4. 转换字节码为基本块
        print("正在将字节码转换为基本块...")
        all_blocks = processor.process_multiple_contracts(contracts_bytecode)
        print(f"成功生成 {len(all_blocks)} 个基本块\n")

        # 5. 构建交易级控制流图(CFG)
        print("正在构建交易级控制流图...")
        cfg_constructor = CFGConstructor(all_blocks)
        tx_cfg = cfg_constructor.construct_cfg(standardized_trace)
        print(f"成功构建交易级CFG，包含 {len(tx_cfg.nodes)} 个节点和 {len(tx_cfg.edges)} 条边\n")

        
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

        # 11. 对tx_cfg 进行折叠
        try:
            # create_merged_cfg 会在原 cfg 上原地合并并返回同一对象
            merged_tx_cfg = create_merged_cfg(tx_cfg)
            merged_tx_dot_path = os.path.join(result_dir, f"transaction_cfg_merged.dot")
            render_transaction(merged_tx_cfg, merged_tx_dot_path)
            print(f"合并后的交易级CFG DOT文件已保存到: {merged_tx_dot_path}")
        except Exception as e:
            # 保护性捕获，避免后处理异常阻断主流程
            print(f"交易级CFG 后处理失败（不影响原始输出）：{e}")

        # 12. 只保留折叠后cfg的节点头
        try:
            merged_head_dot_path = os.path.join(result_dir, f"transaction_cfg_merged_head.dot")
            
            # 从合并后的 DOT 文件生成精简 DOT 文件（删除 instructions）
            strip_instructions_from_dot(merged_tx_dot_path, merged_head_dot_path)
            
            print(f"精简折叠后CFG DOT文件已保存到: {merged_head_dot_path}")
        except Exception as e:
            print(f"生成折叠后精简DOT失败: {e}")

        # 13. 只保留原始cfg的节点头
        try:
            head_only_dot_path = os.path.join(result_dir, f"transaction_cfg_head_only.dot")
            
            # 从原始 DOT 文件生成精简 DOT 文件（删除 instructions）
            strip_instructions_from_dot(tx_dot_path, head_only_dot_path)
            
            print(f"精简原始CFG DOT文件已保存到: {head_only_dot_path}")
        except Exception as e:
            print(f"生成原始精简DOT失败: {e}")

    
        print("\n===== 处理完成 =====")
        print(f"所有结果已保存到: {os.path.abspath(result_dir)}")
        
    except Exception as e:
        print(f"执行失败: {str(e)}")

if __name__ == "__main__":
    main()
