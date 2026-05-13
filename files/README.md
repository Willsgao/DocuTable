# 银行年报PDF解析工具

基于 PyQt5 的桌面应用，支持从 PDF 中提取财务表格并导出为 Excel 格式。

## 功能特性

- **智能检测**：自动识别文本型/图片型 PDF
- **双模式提取**：文本型直接提取，图片型调用多模态 LLM
- **财务表格识别**：自动识别资产负债表、利润表、现金流量表等
- **Excel 导出**：一键导出为标准 Excel 格式
- **可配置 API**：支持配置豆包等视觉大模型 API

## 安装依赖

```bash
pip install PyQt5 PyMuPDF openpyxl requests
```

## 运行

```bash
python main.py
```

## 使用流程

1. 配置 API Key（如需处理图片型 PDF）
2. 上传银行年报 PDF 文件
3. 选择处理模式（自动/仅文本/仅AI）
4. 点击开始处理
5. 预览结果并导出 Excel
