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
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT = 60
PLACEHOLDER = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"


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
            if key and value and value != PLACEHOLDER:
                os.environ.setdefault(key, value)


def get_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key or key == PLACEHOLDER:
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


def ask(messages: list[dict], model: str) -> tuple[str, dict]:
    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")

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
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        sys.exit(f"[HTTP {e.code}] {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"[网络错误] {e.reason}（检查代理/网络，或稍后重试）")
    except TimeoutError:
        sys.exit(f"[超时] {TIMEOUT}s 内没有响应")

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
    args = parser.parse_args()

    messages: list[dict] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})

    # 单次提问
    if args.prompt:
        messages.append({"role": "user", "content": " ".join(args.prompt)})
        answer, usage = ask(messages, args.model)
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
        answer, _ = ask(messages, args.model)
        print(f"\n模型> {answer}\n")
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
