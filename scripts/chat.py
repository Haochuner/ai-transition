#!/usr/bin/env python
"""Day 2 · 最小可用的 DeepSeek CLI。

用法（key 放在仓库根目录的 .env 里，不写在命令行）：
    python scripts/chat.py "用一句话解释什么是容器"
    python scripts/chat.py              # 不带参数 -> 进入连续对话
    python scripts/chat.py -s "你是个严格的审稿人" "帮我挑错"

只用标准库，不需要 pip install 任何东西。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT = 60

# --- 三个「生产化」参数，先有直觉，后面再理解 ---
MAX_TURNS = 10      # 只把最近 10 轮对话发回模型，防止 token 无限增长
MAX_TOKENS = 1024   # 单次回复上限，控制成本
RETRIES = 3         # 一共尝试 3 次（首次 + 2 次重试）
RETRY_STATUS = {429, 500, 502, 503, 504}  # 限流和临时故障，值得重试

def is_placeholder(value: str) -> bool:
    """sk- 后面全是 x，就是 .env.example 里的占位符。"""
    return value.startswith("sk-") and set(value[3:]) == {"x"}

def load_dotenv() -> None:
    """读 .env 里的 KEY=VALUE，写进环境变量。

    先看当前目录，再看仓库根目录（scripts/ 的上一级）。
    已经存在的环境变量优先，不会被文件覆盖。
    """
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not is_placeholder(value):
                os.environ.setdefault(key, value)


def get_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key or is_placeholder(key):
        sys.exit(
            "没有找到可用的 DEEPSEEK_API_KEY。\n"
            f"请打开 {Path(__file__).resolve().parent.parent / '.env'}，\n"
            "把这一行改成你自己的 key（等号后面直接粘贴，不要加引号）：\n"
            "    DEEPSEEK_API_KEY=sk-你的key\n"
            "key 在 platform.deepseek.com → API keys 创建，只显示一次。"
        )
    if not key.startswith("sk-"):
        sys.exit(f"DEEPSEEK_API_KEY 格式不对（应以 sk- 开头），当前值长度 {len(key)}")
    return key


def trim(messages: list[dict], max_turns: int = MAX_TURNS) -> list[dict]:
    """只保留最近 max_turns 轮对话，system 提示词永远保留。

    一轮 = 一条 user + 一条 assistant，所以 10 轮是 20 条消息。
    多轮聊天的 token 是 O(n) 增长的，不裁剪迟早会撞上下文上限。
    """
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    kept = rest[-max_turns * 2 :]
    # 截断后如果开头是 assistant，丢掉它，否则对话从半截开始
    while kept and kept[0]["role"] == "assistant":
        kept.pop(0)
    return system + kept


def ask(messages: list[dict], model: str, max_tokens: int = MAX_TOKENS) -> tuple[str, dict]:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    for attempt in range(RETRIES):
        req = urllib.request.Request(
            API_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {get_key()}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            # 429 限流、5xx 临时故障：退避重试（1s、2s）
            if e.code in RETRY_STATUS and attempt < RETRIES - 1:
                wait = 2**attempt
                print(f"[重试] HTTP {e.code}，{wait}s 后重试（{attempt + 1}/{RETRIES - 1}）", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"[HTTP {e.code}] {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            # 网络抖动、DNS 抖动、超时：同样值得重试
            reason = getattr(e, "reason", e)
            if attempt < RETRIES - 1:
                wait = 2**attempt
                print(f"[重试] 网络错误（{reason}），{wait}s 后重试（{attempt + 1}/{RETRIES - 1}）", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"[网络错误] {reason}（检查代理/网络，或稍后重试）")
        else:
            break
    else:  # pragma: no cover - 上面每条路径都会 break 或 sys.exit
        sys.exit("[失败] 重试次数用尽")

    return data["choices"][0]["message"]["content"], data.get("usage", {})


def main() -> None:
    # Windows 控制台默认可能是 GBK，强制 UTF-8 免得中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    load_dotenv()

    parser = argparse.ArgumentParser(description="DeepSeek 命令行对话")
    parser.add_argument("prompt", nargs="*", help="要问的问题；不填则进入连续对话")
    parser.add_argument("-s", "--system", default=None, help="系统提示词")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"模型，默认 {DEFAULT_MODEL}")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_TOKENS,
        help=f"单次回复的 token 上限，默认 {MAX_TOKENS}",
    )
    args = parser.parse_args()

    messages: list[dict] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})

    # 单次提问
    if args.prompt:
        messages.append({"role": "user", "content": " ".join(args.prompt)})
        answer, usage = ask(trim(messages), args.model, args.max_tokens)
        print(answer)
        if usage:
            print(
                f"\n[token] 输入 {usage.get('prompt_tokens', '?')} / "
                f"输出 {usage.get('completion_tokens', '?')}",
                file=sys.stderr,
            )
        return

    # 连续对话：输入 exit 退出
    print("连续对话模式，输入 exit 退出。\n")
    total_in = total_out = 0
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in {"exit", "quit", "q"}:
            break
        if not line:
            continue
        messages.append({"role": "user", "content": line})
        # 每轮只把最近的对话发回去，历史在本地完整保留（下一轮还能用）
        answer, usage = ask(trim(messages), args.model, args.max_tokens)
        total_in += usage.get("prompt_tokens", 0) or 0
        total_out += usage.get("completion_tokens", 0) or 0
        print(f"\n模型> {answer}\n")
        messages.append({"role": "assistant", "content": answer})

    if total_in or total_out:
        print(f"[本次会话 token] 输入 {total_in} / 输出 {total_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
