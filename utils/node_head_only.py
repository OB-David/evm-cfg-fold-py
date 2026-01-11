# dot_strip_instructions.py (针对你提供的 DOT 文件格式)
import re

def strip_instructions_from_dot(input_dot_path: str, output_dot_path: str):
    """
    从已有 DOT 文件中去掉节点内 instructions，只保留头信息(label里的start/end/terminator),
    保留原有 fillcolor、style、边的 color 不变。
    """
    with open(input_dot_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配节点 label="..." 后面可能紧跟 , fillcolor=... , style=...
    node_pattern = re.compile(r'(\[label=")(.*?)(".*?];)', re.DOTALL)

    def repl(match):
        label_start = match.group(1)
        label_content = match.group(2)
        label_end = match.group(3)

        # 分割 "---------"
        header = label_content.split("---------")[0].strip()

        return f'{label_start}{header}{label_end}'

    new_content = node_pattern.sub(repl, content)

    with open(output_dot_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"精简后的 DOT 文件已保存到: {output_dot_path}")
