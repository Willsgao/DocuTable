"""从 PDF 中提取指定页码，精确只取给出的页号。直接修改下方 src、out、pages 即可。"""
import fitz

src = r"F:\wills\my_softwares\DocuTable\data\input_pdfs/2026-05-07：厦门银行股份有限公司2025年年度报告 .pdf"
out = r"F:/wills/my_softwares/DocuTable/data/input_pdfs/test_subset.pdf"
pages = [31, 32, 33, 34, 112, 113]  # 页号从 1 开始

doc = fitz.open(src)
new = fitz.open()
for p in pages:
    new.insert_pdf(doc, from_page=p-1, to_page=p-1)
new.save(out)
new.close()
doc.close()
print(f"已生成: {out}，共 {len(pages)} 页: {pages}")
