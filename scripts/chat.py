#!/usr/bin/env python
"""Day 2 · 最小可用的 DeepSeek 命令行（CLI）客户端。

这个文件解决一件事：在终端里敲一句话，让 DeepSeek 回答，并把结果打印出来。
它没有任何依赖，只用 Python 标准库（urllib + json），所以 clone 下来就能跑，
不用 pip install，也不用管虚拟环境。

用法（key 放在仓库根目录的 .env 里，不写在命令行）：
    python scripts/chat.py "用一句话解释什么是容器"
    python scripts/chat.py                              # 不带参数 -> 进入连续对话
    python scripts/chat.py -s "你是个严格的审稿人" "帮我挑错"
    python scripts/chat.py --max-tokens 100 "简短说明"   # 限制回复长度

三个设计要点（新手最容易漏、也是后面 RAG/Agent 项目的前提）：
    1. 历史裁剪：只把最近 N 轮对话发回模型，否则 token 随轮数线性增长，最后超上下文；
    2. 退避重试：429 限流、5xx 抖动、网络抖动时等一等再试，而不是直接崩掉；
    3. 输出上限：max_tokens 控制单次回复长度，让成本可预期。

安全约定：key 只从环境变量 / .env 读取，永远不写死在这个文件里。
"""

# ---- 标准库导入，全部是 Python 自带的，不需要 pip install ----
import argparse          # 解析命令行参数（--system、--max-tokens 这些）
import json              # 组装请求体、解析响应
import os                # 读写环境变量
import sys               # stderr 输出、退出码
import time              # 重试之间的等待（time.sleep）
import urllib.error      # HTTPError / URLError，用来区分错误类型
import urllib.request    # 发 HTTP 请求（标准库里最轻量的方式）
from pathlib import Path  # 跨平台拼路径，找 .env 文件用


# ============================================================================
# 可调参数：集中放这里，别散落在函数内部
# ============================================================================

API_URL = "https://api.deepseek.com/chat/completions"  # DeepSeek 对话补全接口（兼容 OpenAI 格式）
DEFAULT_MODEL = "deepseek-chat"   # 默认模型；换成 deepseek-reasoner 走推理模型
TIMEOUT = 60                      # 单次请求超时（秒）。网络慢时别设太小，否则还没回就断了

# --- 三个「生产化」参数，先有直觉，后面再理解为什么 ---
MAX_TURNS = 10      # 只把最近 10 轮对话发回模型，防止 token 无限增长
MAX_TOKENS = 1024   # 单次回复的输出上限，控制成本。注意它是「硬截断」，不是「让模型写短点」
RETRIES = 3         # 一共尝试 3 次 = 首次 + 2 次重试
RETRY_STATUS = {429, 500, 502, 503, 504}  # 这些状态码值得重试：限流 + 服务端临时故障


# ============================================================================
# 配置读取：.env 文件 + 环境变量
# ============================================================================

def is_placeholder(value: str) -> bool:
    """判断一个 key 是不是 .env.example 里的占位符（比如 sk-xxxxxxxx）。

    作用：防止用户忘了改 .env，程序拿着假 key 去打接口，收到一个莫名其妙的 401。
    这里直接给出「你还没填 key」的提示，比 401 好懂。
    """
    # startswith("sk-") 先确认形状像 key；set(value[3:]) == {"x"} 表示 sk- 后面全是 x
    return value.startswith("sk-") and set(value[3:]) == {"x"}


def load_dotenv() -> None:
    """读取 .env 文件里的 KEY=VALUE，塞进环境变量。

    为什么不用 python-dotenv：那要多一个依赖，而我们只需要读几行文本，
    自己写十行就够了。等以后项目复杂了再换库也不迟。

    查找顺序（先找到的先用）：
        1. 当前工作目录的 .env   —— 方便你在临时目录里放不同的 key
        2. 仓库根目录的 .env     —— 正常情况就是这个（scripts/ 的上一级）

    优先级：已经存在的环境变量 > .env 文件。
    也就是说，如果你在终端里 export 过一个 key，文件不会把它覆盖掉。
    这是 dotenv 类工具的通行约定，方便临时覆盖配置。
    """
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]

    for path in candidates:
        if not path.is_file():
            continue  # 没有这个文件就试下一个

        # 按 UTF-8 读；Windows 用户用记事本另存为时要选 UTF-8，不能是 UTF-16
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()

            # 跳过空行、注释行（# 开头）、以及不含等号的行
            if not line or line.startswith("#") or "=" not in line:
                continue

            # 只按第一个等号切分，这样 value 里带 = 也不会被切坏
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()  # 兼容 shell 风格的 export KEY=...

            # 去掉值两边可能带的引号：KEY="sk-xxx" 和 KEY='sk-xxx' 都能读
            value = value.strip().strip('"').strip("'")

            # setdefault：只在环境变量还不存在时才写入（不覆盖命令行里已经设的值）
            # 占位符（sk-xxx...）直接忽略，让后面的 get_key() 给出「请填 key」的提示
            if key and value and not is_placeholder(value):
                os.environ.setdefault(key, value)


def get_key() -> str:
    """取出 API key 并做最小校验；拿不到就直接退出并告诉用户怎么修。

    这里用 sys.exit 而不是抛异常，是因为它属于「配置错误」：
    用户需要的是清晰的一句话，而不是一堆 traceback。
    退出码非 0，方便在脚本里判断「这次运行失败了」。
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    # 情况一：完全没有 key，或者还是 .env.example 里的占位符
    if not key or is_placeholder(key):
        sys.exit(
            "没有找到可用的 DEEPSEEK_API_KEY。\n"
            f"请打开 {Path(__file__).resolve().parent.parent / '.env'}，\n"
            "把这一行改成你自己的 key（等号后面直接粘贴，不要加引号）：\n"
            "    DEEPSEEK_API_KEY=sk-你的key\n"
            "key 在 platform.deepseek.com → API keys 创建，只显示一次。"
        )

    # 情况二：形状不对（比如粘贴时漏了开头、或者混进了空格）
    # 提前拦下来，总比让服务端返回 401 更容易排查
    if not key.startswith("sk-"):
        sys.exit(f"DEEPSEEK_API_KEY 格式不对（应以 sk- 开头），当前值长度 {len(key)}")

    return key


# ============================================================================
# 对话历史裁剪
# ============================================================================

def trim(messages: list[dict], max_turns: int = MAX_TURNS) -> list[dict]:
    """只保留最近 max_turns 轮对话，system 提示词永远保留。

    为什么必须裁剪：
        每轮请求都要把「到目前为止的全部对话」发回给模型，token 消耗是 O(n) 的。
        聊 20 轮以后请求体会变得很大：变慢、变贵，最终直接超上下文长度报错。
        这是所有多轮应用（聊天、RAG、Agent）的第一个坑。

    「一轮」的定义：一条 user 消息 + 一条 assistant 回复，所以 10 轮 = 20 条消息。

    参数：
        messages  : 完整历史，[{role, content}, ...]
        max_turns : 保留几轮，默认用全局 MAX_TURNS

    返回：新的列表（不修改传入的 messages，调用方本地的历史依然完整）
    """
    # system 单独拎出来：它定义人设/规则，丢掉它模型行为会完全变样
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]

    # 负数切片从末尾往前取：保留最后 max_turns*2 条
    kept = rest[-max_turns * 2 :]

    # 边界情况：如果截断后第一条是 assistant，说明对话从半截开始，
    # 模型会看到一个「自己没有提问过的回答」，容易答非所问，所以丢掉它
    while kept and kept[0]["role"] == "assistant":
        kept.pop(0)

    # 注意顺序：system 在前（协议要求），历史在后
    return system + kept


# ============================================================================
# 发请求
# ============================================================================

def ask(messages: list[dict], model: str, max_tokens: int = MAX_TOKENS) -> tuple[str, dict]:
    """把对话发给模型，返回 (回复文本, 用量信息)。

    这个函数只负责「发」这一件事，不负责裁剪历史 —— 裁剪交给调用方决定，
    以后想改成「按 token 数裁剪」或「保留最近工具调用」，改调用处就行。

    参数：
        messages   : 已经裁剪好的消息列表
        model      : 模型名，如 deepseek-chat
        max_tokens : 输出上限

    返回：
        (content, usage)；usage 里通常有 prompt_tokens / completion_tokens / total_tokens

    失败时直接 sys.exit：调用方拿到的一定是有效结果，不用层层判断 None。
    """

    # 请求体照 DeepSeek（OpenAI 兼容）的格式拼：
    #   stream=False 表示一次返回完整结果（流式输出是下一步的改进）
    #   ensure_ascii=False 让中文原样编码，不转成 \uXXXX，请求体更小也更好 Debug
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    # 重试循环：总共 RETRIES 次机会
    for attempt in range(RETRIES):
        # 每次尝试都重新构造 Request：
        # urllib 的 Request 对象带 data 时会被消费，重复 urlopen 同一个对象可能出问题，
        # 重建的成本几乎为零，稳妥优先。
        req = urllib.request.Request(
            API_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {get_key()}",  # Bearer 后面必须有一个空格
                "Content-Type": "application/json",
            },
        )

        try:
            # urlopen 成功即拿到响应；with 保证连接被关闭
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.load(resp)
            break  # 成功，跳出重试循环

        except urllib.error.HTTPError as e:
            # 服务端返回了 HTTP 状态码（4xx / 5xx）
            # 先把错误响应体读出来，里面通常有具体的 message，比状态码有用得多
            detail = e.read().decode("utf-8", "replace")[:600]

            # 429 限流、5xx 临时故障：退避重试（第 1 次等 1s，第 2 次等 2s）
            # 指数退避的意义：服务端过载时，立刻重试只会更堵
            if e.code in RETRY_STATUS and attempt < RETRIES - 1:
                wait = 2**attempt
                print(
                    f"[重试] HTTP {e.code}，{wait}s 后重试（{attempt + 1}/{RETRIES - 1}）",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue

            # 401（key 错）、400（请求格式错）、402（余额不足）等重试也没用，直接退出
            sys.exit(f"[HTTP {e.code}] {detail}")

        except (urllib.error.URLError, TimeoutError) as e:
            # 根本没连上：DNS 失败、代理问题、断网、超时，都属于这一类
            # URLError 里真正的错误在 .reason，TimeoutError 没有，所以用 getattr 兜底
            reason = getattr(e, "reason", e)

            if attempt < RETRIES - 1:
                wait = 2**attempt
                print(
                    f"[重试] 网络错误（{reason}），{wait}s 后重试（{attempt + 1}/{RETRIES - 1}）",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue

            sys.exit(f"[网络错误] {reason}（检查代理/网络，或稍后重试）")

        # Python 的 try/except 可以带 else：只有没出异常才执行。
        # 这里什么都不做，只是为了表达「成功路径到此结束」。
        else:
            break

    # for...else：循环「正常跑完、没有 break」才会进 else。
    # 正常情况不会走到这里（每条失败路径都 sys.exit 或 continue），
    # 保留它是为了兜底，避免真的发生时代码拿着未定义的 data 往下走。
    else:  # pragma: no cover
        sys.exit("[失败] 重试次数用尽")

    # 响应结构：choices[0].message.content 是正文
    # usage 可能缺失（极少数情况），用 .get 给个空 dict，调用方不会炸
    return data["choices"][0]["message"]["content"], data.get("usage", {})


# ============================================================================
# 命令行入口
# ============================================================================

def main() -> None:
    # Windows 的终端默认可能是 GBK（936 代码页），中文输出会变乱码。
    # 这里把 stdout/stderr 强制成 UTF-8；reconfigure 在旧版本 Python 上不存在，
    # 所以包一层 try，失败也不影响主流程。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # 先读 .env，再解析参数：这样命令行参数里可以引用环境变量里的东西（以后会用到）
    load_dotenv()

    parser = argparse.ArgumentParser(description="DeepSeek 命令行对话")
    # nargs="*" 表示可以给 0 个或多个词，程序会自动用空格拼成一句话
    parser.add_argument("prompt", nargs="*", help="要问的问题；不填则进入连续对话")
    # 系统提示词：设定人设/规则，比每轮重复写「你是个...」省事
    parser.add_argument("-s", "--system", default=None, help="系统提示词")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"模型，默认 {DEFAULT_MODEL}")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=MAX_TOKENS,
        help=f"单次回复的 token 上限，默认 {MAX_TOKENS}",
    )
    args = parser.parse_args()

    # messages 是「完整历史」，本地一直保留；
    # 每次发请求时再用 trim() 取最近的一部分，两者不要混为一谈。
    messages: list[dict] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})

    # ---- 模式一：单次提问（给了一句话，问完就退出）----
    if args.prompt:
        messages.append({"role": "user", "content": " ".join(args.prompt)})
        # trim 在这里可能看起来多余（单轮只有一条），但保持和交互模式同一套逻辑，行为一致
        answer, usage = ask(trim(messages), args.model, args.max_tokens)
        print(answer)

        # 用量打到 stderr，这样把 stdout 重定向到文件时，正文里不会混进统计数据
        if usage:
            print(
                f"\n[token] 输入 {usage.get('prompt_tokens', '?')} / "
                f"输出 {usage.get('completion_tokens', '?')}",
                file=sys.stderr,
            )
        return

    # ---- 模式二：连续对话（REPL）----
    print("连续对话模式，输入 exit 退出。\n")
    total_in = total_out = 0  # 累计本次会话的 token，退出时汇总

    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C 或输入流结束（比如管道输入用完）：干净退出，不要打印 traceback
            print()
            break

        if line.lower() in {"exit", "quit", "q"}:
            break
        if not line:
            continue  # 直接回车，跳过

        messages.append({"role": "user", "content": line})

        # 关键：发出去的是 trim(messages)（裁剪后的副本），
        # 但本地 messages 继续累积完整历史 —— 这样上下文不丢，只是不全部发出去。
        answer, usage = ask(trim(messages), args.model, args.max_tokens)

        # usage 里的字段可能缺失，用 0 兜底，避免 None + int 报错
        total_in += usage.get("prompt_tokens", 0) or 0
        total_out += usage.get("completion_tokens", 0) or 0

        print(f"\n模型> {answer}\n")

        # 把模型回复也存进历史，否则下一轮模型不知道上一轮自己说了什么
        messages.append({"role": "assistant", "content": answer})

    if total_in or total_out:
        print(f"[本次会话 token] 输入 {total_in} / 输出 {total_out}", file=sys.stderr)


# 只有「直接运行这个文件」时才执行 main()；
# 被别的脚本 import 时不会自动跑，方便以后把 ask() 当函数库复用。
if __name__ == "__main__":
    main()
