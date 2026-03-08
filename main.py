import datetime
import os

print("--- 开始运行极简测试脚本 ---")

# 获取当前时间
current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
content = f"恭喜！这是一份由 GitHub Actions 自动生成的文件。\n生成时间: {current_time}\n"

# 写入文件
file_name = "test_output.txt"
with open(file_name, "w", encoding="utf-8") as f:
    f.write(content)

print(f"✅ 文件 {file_name} 生成成功！大小: {os.path.getsize(file_name)} 字节")
