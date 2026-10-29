import tkinter as tk
import random

class MemoryCardGame:
    def __init__(self, root):
        self.root = root
        self.root.title("数据核对清单")
        self.root.geometry("720x540")

        self.card_count = 4
        self.max_cards = 26
        self.is_showing = False
        self.target_idx = None
        self.card_numbers = []

        info_frame = tk.Frame(root)
        info_frame.pack(pady=8)
        self.info_label = tk.Label(info_frame, text=f"卡牌数量：{self.card_count} | 准备开始", font=("微软雅黑",12))
        self.info_label.pack()

        self.board_frame = tk.Frame(root)
        self.board_frame.pack(pady=10)
        ctrl_frame = tk.Frame(root)
        ctrl_frame.pack(pady=5)
        self.start_btn = tk.Button(ctrl_frame, text="开始演示", command=self.start_show, font=("微软雅黑",11))
        self.start_btn.pack()

        self.build_board()

    def build_board(self):
        # 清空旧面板
        for w in self.board_frame.winfo_children():
            w.destroy()
        self.card_btns = []
        # 生成1~99不重复数字
        self.card_numbers = random.sample(range(1,100), self.card_count)
        cols = 6
        for i in range(self.card_count):
            btn = tk.Button(
                self.board_frame,
                text="?",
                width=5, height=2,
                font=("微软雅黑",13),
                bg="#cccccc",
                state=tk.DISABLED,
                command=lambda idx=i: self.on_card_click(idx)
            )
            r = i // cols
            c = i % cols
            btn.grid(row=r, column=c, padx=4, pady=4)
            self.card_btns.append(btn)

    def start_show(self):
        if self.is_showing:
            return
        self.is_showing = True
        self.start_btn.config(state=tk.DISABLED)
        self.info_label.config(text="观看卡牌闪现，记住目标数字！")
        self.target_idx = random.randint(0, self.card_count - 1)
        self.root.after(100, self.show_one, 0)

    def show_one(self, i):
        # 全部重置为问号
        for btn in self.card_btns:
            btn.config(text="?", bg="#cccccc")

        if i >= self.card_count:
            # 闪现完毕，进入猜牌阶段
            self.info_label.config(text=f"演示结束！找出数字【{self.card_numbers[self.target_idx]}】的卡片")
            for btn in self.card_btns:
                btn.config(state=tk.NORMAL)
            self.is_showing = False
            return

        num = self.card_numbers[i]
        if i == self.target_idx:
            self.card_btns[i].config(text=str(num), bg="#ffcccc")
        else:
            self.card_btns[i].config(text=str(num), bg="#e6edff")
        self.root.after(1000, self.show_one, i+1)

    def on_card_click(self, idx):
        if self.is_showing:
            return
        # 全部禁用按钮
        for btn in self.card_btns:
            btn.config(state=tk.DISABLED)

        target_num = self.card_numbers[self.target_idx]
        if idx == self.target_idx:
            if self.card_count < self.max_cards:
                self.card_count += 2
                msg = f"✅猜对！目标数字：{target_num}，卡牌+2，当前：{self.card_count}"
            else:
                msg = f"✅猜对！目标数字：{target_num}，已达最大26张！"
        else:
            msg = f"❌猜错！目标数字是：{target_num}，卡牌数量保持 {self.card_count}"

        self.info_label.config(text=msg)
        self.root.after(1400, self.reset_round)

    def reset_round(self):
        self.build_board()
        self.info_label.config(text=f"卡牌数量：{self.card_count}，点击【开始演示】")
        self.start_btn.config(state=tk.NORMAL)

if __name__ == "__main__":
    win = tk.Tk()
    app = MemoryCardGame(win)
    win.mainloop()
