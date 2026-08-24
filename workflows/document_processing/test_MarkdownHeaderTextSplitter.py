"""
Date: 2025-12-17 13:44:13
LastEditTime: 2025-12-17 13:44:25
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""

from langchain_text_splitters import MarkdownHeaderTextSplitter

markdown_document = """
# 项目介绍

## 功能特性

- 支持多种格式
- 高性能处理

## 安装方法

### 通过 pip 安装

```bash
pip install package
```
"""

# 定义要识别的标题层级
headers_to_split_on = [
    ("#", "Header 1"),
    ("##", "Header 2"),
    ("###", "Header 3"),
]

# 方式1：默认移除标题
splitter1 = MarkdownHeaderTextSplitter(headers_to_split_on)
docs1 = splitter1.split_text(markdown_document)

# 方式2：保留标题在内容中
splitter2 = MarkdownHeaderTextSplitter(headers_to_split_on, strip_headers=False)
docs2 = splitter2.split_text(markdown_document)

# 对比输出
for i, doc in enumerate(docs1):
    print(f"--- 文档块 {i+1} (strip_headers=True) ---")
    print(f"内容:\n{doc.page_content[:100]}...")
    print(f"元数据: {doc.metadata}\n")

for i, doc in enumerate(docs2):
    print(f"--- 文档块 {i+1} (strip_headers=False) ---")
    print(f"内容:\n{doc.page_content[:100]}...")
    print(f"元数据: {doc.metadata}\n")