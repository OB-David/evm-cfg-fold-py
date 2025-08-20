import re
import os # 导入os模块
import glob

def find_call_nodes(dot_file):
    call_nodes = []
    node_pattern = re.compile(r'"(block_[^"]+)" \[label="(.+?)"\]', re.DOTALL)
    call_instrs = ['CALL', 'SSTORE']

    try:
        with open(dot_file, encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: The file '{dot_file}' was not found.")
        return []
    except OSError as e:
        print(f"An OS error occurred: {e}")
        return []

    for match in node_pattern.finditer(content):
        node_name = match.group(1)
        node_label = match.group(2)
        # 提取指令部分
        instr_lines = node_label.split('---------')[-1].split('\\n')
        for line in instr_lines:
            for instr in call_instrs:
                # 只要行里有 CALL 相关指令
                if re.search(r'\b{}\b'.format(instr), line):
                    call_nodes.append((node_name, line.strip()))
                    break
    return call_nodes

if __name__ == '__main__':
    dot_file_path = input("Please enter the path to the .dot file: ").strip('"')
    print(f'文件: {dot_file_path}')

    call_nodes = find_call_nodes(dot_file_path)

    if call_nodes:
        # 获取原文件的文件名
        original_file_name = os.path.basename(dot_file_path)
        
        # 定义输出文件夹和文件名
        output_dir = "Result_call_nodes"
        output_file_name = "call_" + os.path.splitext(original_file_name)[0] + ".txt"
        output_path = os.path.join(output_dir, output_file_name)

        # 如果输出文件夹不存在，则创建
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        # 将结果写入文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f'文件: {dot_file_path}\n')
            for node, instr in call_nodes:
                f.write(f'  节点: {node}  指令: {instr}\n')
            f.write('-' * 40 + '\n')
        
        print(f"\n成功找到 {len(call_nodes)} 个调用节点，结果已保存到：{output_path}")
    else:
        print(f"\n在文件 '{dot_file_path}' 中未找到任何包含调用指令的节点。")
    print('-' * 40)