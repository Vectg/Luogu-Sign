# luogu-sign

洛谷（luogu.com.cn）每日自动打卡，基于 Gitee Go 流水线定时运行，支持多账号。

## 工作原理

1. 从环境变量读取洛谷登录 cookie，携带 cookie 访问首页获取 `csrf-token`
2. POST 到 `/index/ajax_punch` 完成打卡
3. 自动处理 Cloudflare C3VK 轻量 cookie 挑战（等价浏览器行为）

## 前置条件

- Gitee 账号，仓库已开通 Gitee Go 流水线
- 洛谷账号的登录 cookie（从浏览器开发者工具获取）

## 快速开始

### 1. 获取 Cookie

浏览器登录 [luogu.com.cn](https://www.luogu.com.cn)，F12 → Application → Cookies → 复制 `__client_id` 和 `_uid` 的值，拼接为：

```
__client_id=xxxxxxxx;_uid=123456;
```

### 2. Fork 仓库

Fork 本仓库到你的 Gitee 账号下。

### 3. 配置变量

进入 Gitee 仓库 → **流水线** → **通用变量**，添加变量（类型选「密文」）：

| 变量名 | 值 |
|--------|-----|
| `COOKIE_1` | 第一个账号的 cookie |
| `COOKIE_2` | 第二个账号的 cookie（可选） |
| ... | 按需添加，最多 10 个 |

单账号也可用 `COOKIE` 变量名（兼容旧用法）。

### 4. 启用定时运行

流水线页面确认触发器 `schedule` 已生效（每天 08:00 北京时间自动运行），手动运行一次测试。

`.workflow/work.yml` cron 表达式：`0 0 8 * * ? *`（Quartz 7 段，每天 08:00）。

## 多账号支持

脚本自动扫描所有 `COOKIE_<数字>` 环境变量，按编号升序收集非空值。不要求编号连续，空变量自动跳过。

```
COOKIE_1='__client_id=aaa;_uid=111;'
COOKIE_3='__client_id=bbb;_uid=222;'
COOKIE_7='__client_id=ccc;_uid=333;'
```

以上配置会依次为账号 1、3、7 打卡。需要更多账号时在 work.yml 的 `variables.global` 中追加声明即可。

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 全部成功 |
| 1 | 网络错误或 CDN 拦截 |
| 2 | 未配置任何 cookie 变量 |
| 3 | cookie 已过期，需重新获取 |

多账号时退出码取最严重值，任一失败整个流水线标红。

## 目录结构

```
.
├── luogu-auto-punch.py   # 打卡脚本
└── .workflow/
    └── work.yml          # Gitee Go 流水线配置
```

## License

MIT
