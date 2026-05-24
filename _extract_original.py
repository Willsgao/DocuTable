"""从 git 历史提取原始代码"""
import subprocess
result = subprocess.run(
    ["git", "show", "df97b10:codes/pdf_extractor/processor.py"],
    capture_output=True, text=True, encoding='utf-8'
)
lines = result.stdout.split('\n')

# 提取 _extract_tables_via_docx (行 917-1111, 0-indexed: 916-1110)
start = 916
end = 1111
with open('temp_docx_method.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines[start:end]))

# 提取 _verify_docx_page_numbers (行 1112-1221, 0-indexed: 1111-1220)
start2 = 1111
end2 = 1221
with open('temp_verify_method.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines[start2:end2]))

print("Done")
