import os
import pandas as pd
from tkinter import Tk, filedialog, messagebox

def merge_excel_files():
    root = Tk()
    root.withdraw()
    # 选择文件夹
    folder_path = filedialog.askdirectory(title="选择存放Excel文件的文件夹")
    if not folder_path:
        messagebox.showinfo("提示", "你没有选择文件夹，程序退出")
        return

    all_data = []
    header = None
    # 遍历文件夹里所有excel
    for filename in os.listdir(folder_path):
        if filename.endswith((".xlsx", ".xls")) and not filename.startswith("合并结果"):
            file_full_path = os.path.join(folder_path, filename)
            df = pd.read_excel(file_full_path)
            if header is None:
                header = df.columns
                all_data.append(df)
            else:
                all_data.append(df)
    if len(all_data) ==0:
        messagebox.showerror("错误", "文件夹里面没有找到Excel文件！")
        return
    result_df = pd.concat(all_data, ignore_index=True)
    out_file = os.path.join(folder_path, "✅合并结果.xlsx")
    result_df.to_excel(out_file, index=False)
    messagebox.showinfo("完成", f"合并成功！\n文件输出：\n{out_file}")

if __name__ == "__main__":
    merge_excel_files()
