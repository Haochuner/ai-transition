# ai-transition

我的 AI 转型记录：从一个跟代码不太沾边的岗位，转向能用 AI 做出东西的人。

这个仓库不追求好看，只追求**每天有东西进来**。

## 这里放什么

| 目录 | 内容 |
| --- | --- |
| `README.md` | 总入口：我在干什么、进度到哪 |
| `notes/` | 每天一条学习日志，写清楚学了什么、卡在哪、怎么解决的 |
| `scripts/` | 练习用的小脚本，跑得通就行，不要求优雅 |
| `projects/` | 攒够了就做成一个能跑的小项目 |

## 进度

- [x] 装 Python（3.14.2）
- [x] 装 Git（2.39.2）
- [x] 注册 DeepSeek，充值 100
- [x] 建仓库，写第一份 README
- [x] 装 Docker，跑通 hello-world（数据目录已放 E 盘）
- [x] 第一次 push 到 GitHub（Haochuner/ai-transition）
- [ ] Day 2：写出并跑通第一个 CLI（`scripts/chat.py`）

## 环境

- 系统：Windows 11（23H2）
- Python 3.14.2（`python` / `py` 都能用）
- Git 2.39.2
- 编辑器：VS Code
- 模型：DeepSeek（API 调用 + 日常问答）

## 怎么用这个仓库

```powershell
git clone https://github.com/<你的用户名>/ai-transition.git
cd ai-transition
```

日常提交：

```powershell
git add .
git commit -m "day1: 建仓库，写 README"
git push
```

## 下一步

1. 跑通 `scripts/chat.py`（见下），确认 DeepSeek API 全链路可用
2. 每天一条 `notes/` 日志，坚持 30 天

## 第一个 CLI

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."        # 只在这个窗口有效
python scripts/chat.py "用一句话解释什么是容器"
python scripts/chat.py                   # 连续对话
```

只用标准库，不用 `pip install`。key 不写进代码，只从环境变量读。

---

从 2026 年 10 月开始。慢没关系，别停。
