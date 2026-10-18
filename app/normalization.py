"""保守名称清理；规范化值从来不是人物标识或合并依据。"""

import unicodedata


def normalize_name(value: str) -> str:
    # 不转换简繁、异体字、全半角、大小写，不删除内部空白或标点。
    return unicodedata.normalize("NFC", value.strip())

