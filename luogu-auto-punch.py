#!/usr/bin/env python3
"""洛谷（luogu.com.cn）每日打卡脚本

修复说明（相对旧脚本）：
- 用正则提取 csrf-token，替代硬编码字节偏移。旧逻辑在页面结构变化时会把
  页面内容误当作 token 塞进请求头，导致 InvalidHeader 崩溃。
- 检测 Cloudflare 挑战页面：managed 挑战直接给出明确报错，不再解析错误内容。
- 处理 Cloudflare 轻量 cookie 挑战（C3VK）：保存 challenge cookie 后重试，
  等价于浏览器的自动 cookie 行为，不涉及任何绕过风控手段。
- 请求失败自动重试，输出结构化结果，退出码可被 CI 判断。

用法（cookie 从环境变量读取，避免明文出现在运行日志）：
    单账号：   export COOKIE='<cookie>'
    多账号：   export COOKIE_1='<cookie1>'
               export COOKIE_2='<cookie2>'
    然后运行： python luogu-auto-punch.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

HOME_URL = "https://www.luogu.com.cn"
PUNCH_URL = "https://www.luogu.com.cn/index/ajax_punch"

REQUEST_TIMEOUT = 20
MAX_ATTEMPTS = 3

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Referer": HOME_URL + "/",
}

MANAGED_MARKERS = ("Just a moment", "challenge-platform", "__cf_chl_")


def parse_cookies(cookie_str):
    cookies = {}
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


def is_managed_challenge(text):
    return any(marker in text for marker in MANAGED_MARKERS)


def make_session(cookie_str):
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.cookies.update(parse_cookies(cookie_str))
    return session


def fetch_homepage(session):
    """获取首页 HTML。处理轻量 cookie 挑战，返回 (html, error)。"""
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = session.get(HOME_URL, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None, "NETWORK_ERROR: %s" % exc

        text = resp.text
        if is_managed_challenge(text):
            return None, "CLOUDFLARE_MANAGED_CHALLENGE: 洛谷 CDN 返回受控人机校验页，当前出口 IP 无法直接访问"
        if "C3VK" in text and "window.open" in text and "cookie=" in text:
            match = re.search(r'cookie="([^;]+);', text)
            if match:
                name, value = match.group(1).split("=", 1)
                session.cookies.set(name, value, domain="www.luogu.com.cn", path="/")
                continue
        return text, None
    return None, "CHALLENGE_LOOP: 多次请求仍未通过 Cloudflare 校验"


def extract_csrf(html):
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return match.group(1) if match else None


def punch(cookie_str):
    session = make_session(cookie_str)
    html, error = fetch_homepage(session)
    if error:
        return {"ok": False, "stage": "fetch_homepage", "error": error}

    token = extract_csrf(html)
    if not token:
        return {"ok": False, "stage": "extract_csrf", "error": "NO_CSRF_TOKEN: 页面中未找到 csrf-token，页面结构可能已变化"}

    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = session.post(
                PUNCH_URL,
                headers={
                    "Origin": HOME_URL,
                    "Referer": HOME_URL + "/",
                    "X-CSRF-Token": token,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return {"ok": False, "stage": "punch", "error": "NETWORK_ERROR: %s" % exc}

        if is_managed_challenge(resp.text):
            return {"ok": False, "stage": "punch", "error": "CLOUDFLARE_MANAGED_CHALLENGE: 打卡请求被 CDN 人机校验拦截"}
        try:
            return {"ok": True, "stage": "punch", "response": resp.json()}
        except ValueError:
            return {"ok": True, "stage": "punch", "response": {"raw": resp.text[:500]}}
    return {"ok": False, "stage": "punch", "error": "RETRY_EXHAUSTED"}


def interpret(response):
    """把打卡响应翻译成可读结论，返回 (message, exit_code)。"""
    if not isinstance(response, dict):
        return ("打卡请求已执行，但响应不是 JSON，请查看上方原始输出。", 0)

    code = response.get("code") or response.get("status")
    message = response.get("message") or response.get("data") or ""

    session_lost = any(word in str(message) for word in ("会话超时", "请先登录", "未登录", "请登录", "会话已过期"))
    if code == 200:
        return ("打卡成功，结果见上方响应。", 0)
    if session_lost:
        return ("会话已过期：请到洛谷网页重新获取 COOKIE，并更新 Gitee Go 流水线的 COOKIE 变量。", 3)
    return ("打卡请求已执行，平台返回如下，请查看上方响应。", 0)


def collect_cookies():
    """自动收集所有已配置账号的 cookie。

    扫描所有 COOKIE_<数字> 环境变量（如 COOKIE_1、COOKIE_2...），按编号升序收集非空值，
    不要求编号连续，也不设固定上限。同时兼容旧用法 COOKIE_0 和 COOKIE。
    返回 [(编号, cookie), ...]，空账号自动跳过。
    """
    raw = {}
    for key, value in os.environ.items():
        if key.startswith("COOKIE_"):
            no_str = key[len("COOKIE_"):]
            if no_str.isdigit():
                raw[int(no_str)] = value.strip()

    accounts = []
    for no in sorted(raw):
        if raw[no]:
            accounts.append((no, raw[no]))
    if not accounts and os.environ.get("COOKIE", "").strip():
        accounts.append((1, os.environ["COOKIE"].strip()))
    return accounts


def main(argv):
    accounts = collect_cookies()
    if not accounts:
        print("usage: set COOKIE (单账号) 或 COOKIE_1、COOKIE_2...（多账号）环境变量")
        return 2

    print("Start punching at %s, %d account(s)" % (time.strftime("%Y-%m-%d %H:%M:%S"), len(accounts)))
    worst_exit = 0
    for no, cookie in accounts:
        print("\n===== account %d =====" % no)
        result = punch(cookie)
        print(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            print("PUNCH FAILED: %s" % result.get("error"))
            worst_exit = max(worst_exit, 1)
            continue
        message, exit_code = interpret(result.get("response"))
        print("RESULT: %s" % message)
        worst_exit = max(worst_exit, exit_code)
    print("\nDone. %d account(s), worst exit code = %d" % (len(accounts), worst_exit))
    return worst_exit


if __name__ == "__main__":
    sys.exit(main(sys.argv))
