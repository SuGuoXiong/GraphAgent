"""Token 估算工具 —— 基于字符比例的粗略 Token 计数。

采用字符比例法，避免引入外部 tokenizer 依赖：
- 中文字符: 1 token ≈ 1.5 字符
- 英文/代码: 1 token ≈ 4 字符
- 混合文本: 取加权平均
"""

import re

# Unicode 范围：CJK 统一表意文字
_CJK_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')


def estimate_tokens(text: str) -> int:
    """估算文本的 Token 数。

    对中文字符赋予更高权重（1 token ≈ 1.5 字符），
    英文字符 / 代码赋予较低权重（1 token ≈ 4 字符）。
    多行文本、特殊字符按英文规则处理。
    """
    if not text:
        return 0
    chinese_chars = len(_CJK_RE.findall(text))
    other_chars = max(len(text) - chinese_chars, 0)
    return int(chinese_chars / 1.5 + other_chars / 4)
