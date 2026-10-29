import os
import sys
import pandas as pd
from tkinter import Tk, filedialog, messagebox

# ========== 试用版限制配置 ==========
MAX_TRIALS = 3                    # 最多合并次数
TRIAL_FILE = "试用次数.dat"        # 记录次数的本地文件（和exe放一起）
MAX_FILES = 5                     # 每次最多合并文件数

def get_trial_dir():
    # 打包成exe后，取exe所在目录存次数文件（用户看得见，透明诚实）
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def read_count():
    path = os.path.join(get_trial_dir(), TRIAL_FILE)
    try:
        with open(path, 'r') as f:
            return int(f.read().strip())
    except:
        return 0

def write_count(n):
    path = os.path.join(get_trial_dir(), TRIAL_FILE)
    with open(path, 'w') as f:
        f.write(str(n))

def merge_excel_files():
    # ===== 次数校验：超过3次直接锁定 =====
    used = read_count()
    if used >= MAX_TRIALS:
        messagebox.showwarning("试用已结束",
            f"试用版最多免费使用 {MAX_TRIALS} 次，你已用完。\n\n"
            "如需继续使用，请购买正式版（无次数限制、无文件数限制）。")
        return

    root = Tk()
    root.withdraw()
    folder_path = filedialog.askdirectory(title="选择存放Excel文件的文件夹")
    if not folder_path:
        messagebox.showinfo("提示", "你没有选择文件夹，程序退出")
        return

    all_data = []
    header = None
    file_count = 0

    for filename in os.listdir(folder_path):
        if filename.endswith((".xlsx", ".xls")) and not filename.startswith("合并结果"):
            if file_count >= MAX_FILES:
                messagebox.showwarning("试用版限制",
                    f"试用版每次最多合并 {MAX_FILES} 个文件！\n购买正式版可合并全部文件。")
                break
            file_full_path = os.path.join(folder_path, filename)
            df = pd.read_excel(file_full_path)
            current_cols = list(df.columns)
            if header is None:
                header = current_cols
                all_data.append(df)
            else:
                if current_cols != header:
                    messagebox.showwarning("表头警告",
                        f"【{filename}】表头和第一张表格不一致！\n合并后会产生空白列。")
                all_data.append(df)
            file_count += 1

    if len(all_data) == 0:
        messagebox.showerror("错误", "文件夹里面没有找到Excel文件！")
        return

    result_df = pd.concat(all_data, ignore_index=True)
    out_file = os.path.join(folder_path, "✅合并结果.xlsx")
    result_df.to_excel(out_file, index=False)

    # ===== 合并成功，次数+1 =====
    write_count(used + 1)
    left = MAX_TRIALS - (used + 1)
    messagebox.showinfo("完成",
        f"合并成功！\n文件输出：\n{out_file}\n\n"
        f"（试用版剩余次数：{left} 次）")

if __name__ == "__main__":
    merge_excel_files()
