# TVBox 自建源 · 部署指南

把一堆散乱的 TVBox 源 JSON **合并去重**，定时**自动探测存活、剔除失效源**，再推送到你自己的 git 仓库。TVBox 直接填仓库地址就能用。

> **不需要 nginx、不需要域名、不需要 HTTPS。** 源文件由 git 推到仓库，仓库的 raw 地址就是 TVBox 的源地址。

---

## 一、它怎么工作

```
   定时任务(cron/systemd) 或 网页点「构建并推送」
          │
          ▼
    build.py ──► 读本地源 + dc.txt 远程源
              ──► 合并去重 ──► 并发探测存活 ──► 剔除失效源
              ──► 生成 www/ 下的源文件
          │
          ▼
    git push ──► 你的仓库(cnb.cool / GitHub)
          │
          ▼
    TVBox 填仓库 raw 地址
```

常用产物（都在 `www/` 下）：

| 文件 | 用途 |
|---|---|
| `tvbox.json` | **主源**。失效源已剔除，恢复后自动放回。电视/手机填这个 |
| `tvbox.lite.json` | **稳妥源**。只含实测存活、不依赖 App 爬虫，App 版本不确定时用 |

> 源文件是**只读输入，脚本永不修改或删除**。今天失效的源只是被暂时注释掉，明天恢复了会自动回到主源，你不用管。

---

## 二、五分钟部署

### 1. 装依赖

```bash
# Debian / Ubuntu / fnOS 等
sudo apt update
sudo apt install -y python3 curl git

# 确认有 bash 和 flock(定时脚本要用)
bash --version && command -v flock
```

> 网页管理后台 `manage.py` 是**纯标准库、零依赖**，不需要 Flask；抓取走内置 `urllib`，`requests` 也不是必须。

### 2. 放项目

把整个项目目录传到服务器（源 JSON、`tvbox_core/`、`build.py`、`manage.py`、`deploy/`、`dc.txt` 都要）。示例放在 `/opt/tvbox`：

```
/opt/tvbox/
├─ build.py  manage.py      # 入口
├─ tvbox_core/              # 核心代码
├─ sources/  *.json         # 你的本地源文件(只读输入)
├─ dc.txt                   # 远程源清单
├─ deploy/                  # update.sh / push.sh / check.sh / *.service
└─ www/                     # 构建产物(自动生成)
```

> 脚本会自动按自身位置推导目录，放 `/opt/tvbox`、`/home/xxx/tvbox` 都行，**不用改代码**。直接用普通用户跑即可。

### 3. 初始化 git 并首次推送

压缩包不含 `.git`，服务器上还不是 git 仓库，需要先初始化并推一次。

**方式 A：SSH deploy key（推荐，服务器不存明文令牌）**

```bash
cd /opt/tvbox
git init -b main
git config user.email "you@example.com"
git config user.name  "your-name"

# 生成部署密钥, 把公钥贴到仓库设置 →「部署公钥」
ssh-keygen -t ed25519 -C tvbox-bot -f ~/.ssh/tvbox_deploy -N ''
cat ~/.ssh/tvbox_deploy.pub

# 关联远端并首次推送
git remote add origin git@cnb.cool:<账号>/<仓库名>.git
git add -A && git commit -m "init: 首次部署"
git push -u origin main
```

**方式 B：HTTPS + 令牌（PAT）**

令牌放在**已被 gitignore 的** `deploy/push.env` 里，脚本自动读取：

```bash
cd /opt/tvbox
git init -b main
git config user.email "you@example.com"
git config user.name  "your-name"

# 1. 本地凭证文件(不会被提交)
cat > deploy/push.env <<'EOF'
GIT_USER=<你的用户名>
GIT_TOKEN=<你的 Personal Access Token>
GIT_REMOTE=origin
GIT_BRANCH=main
EOF

# 2. 关联远端(地址里不要写令牌)
git remote add origin https://cnb.cool/<账号>/<仓库名>.git

# 3. 首次提交并推送(令牌从 push.env 读, 临时注入, 不进 .git/config)
git add -A && git commit -m "init: 首次部署"
set -a; . deploy/push.env; set +a
git -c "url.https://$GIT_USER:$GIT_TOKEN@cnb.cool/.insteadOf=https://cnb.cool/" push -u origin main
```

> 之后 `deploy/push.sh`、`update.sh` 会自动读 `deploy/push.env`，令牌临时注入、**不写进 `.git/config`**。
>
> 若 push 报 `rejected ... fetch first`，说明远端已有旧内容。想以本地为准直接覆盖，把上面的 `push` 换成 `push --force`。

### 4. 先手动构建一次

```bash
cd /opt/tvbox
python3 build.py --out www
```

产物会出现在 `www/`。

### 5. TVBox 填地址

```
https://cnb.cool/<账号>/<仓库名>/-/git/raw/main/www/tvbox.json
```

> 必须是 `/-/git/raw/` 真实文件地址，不是 `/-/blob/`（那个返回网页）。

---

## 三、企业微信推送通知（可选）

想在每次构建后收到推送（**成功推结果摘要，异常推告警**），按下面 4 步配置。

### 1. 拿到机器人凭证 bot_id 和 secret

去**企业微信管理后台**创建一个「**智能机器人**」（长连接 API 模式），创建完成后会给你两样东西：

- `bot_id`
- `secret`

复制下来备用。协议文档见企业微信开发者中心 `path/101463`（以你后台实际菜单为准）。

### 2. 填进配置

推荐写进 `notify_config.json`（该文件已在 `.gitignore`，**不会被提交**）：

```json
{
  "enabled": true,
  "bot_id": "你的 bot_id",
  "secret": "你的 secret",
  "chatid": "",
  "chat_type": 1
}
```

> `chatid` / `chat_type` **不用手填**，第 3 步绑定后会自动写进去。
> 也可以用环境变量代替：`TVBOX_BOT_ID` / `TVBOX_BOT_SECRET`。

### 3. 绑定会话（首次必须做一次）

企业微信协议规定：机器人**只能给你主动发过消息的会话**推送，所以要绑定一次：

```bash
cd /opt/tvbox
python3 build.py --notify-capture
# 运行后, 在 3 分钟内在企业微信里给这个机器人发任意一条消息
# 收到机器人回复「✅ 已绑定 TVBox 构建通知」就成功了
```

绑定结果写入 `notify_config.json`，之后定时构建会自动推送，无需再操作。

### 4. 测试

```bash
python3 build.py --notify-test      # 发一条测试消息验证
```

不想收推送时：`python3 build.py --no-notify`，或把 `notify_config.json` 的 `enabled` 改成 `false`。

> **不用 ssh 也能确认**：网页管理后台「总览 → 企业微信通知」直接显示绑定状态，还有「发送测试消息」按钮。

### 凭证存放位置一览（都不进仓库）

| 凭证 | 放哪里 | 谁读取 |
|---|---|---|
| git 推送令牌 | `deploy/push.env` 或环境变量 `GIT_TOKEN` / `GIT_USER` | `deploy/push.sh`、`update.sh` |
| 企业微信 bot_id/secret | `notify_config.json` 或 `TVBOX_BOT_ID` / `TVBOX_BOT_SECRET` | `tvbox_core/notify.py` |
| 通知会话 chatid | `notify_config.json`（绑定后自动写入） | `tvbox_core/notify.py` |
| SSH 推送密钥 | `~/.ssh/tvbox_deploy`（仓库只存公钥） | git |

---

## 四、定时自动更新（可选）

不想每次手点，就让系统定时跑 `deploy/update.sh`（它会 **构建 → 构建前 git pull → 构建后 git push**）。

**方式 A：cron（最简单）**

```bash
crontab -e
# 每天 04:17 自动重建(避开整点)
17 4 * * * /opt/tvbox/deploy/update.sh
```

**方式 B：systemd timer（日志更清晰）**

```bash
# 先把 service 里的 WorkingDirectory/ExecStart 路径和 User 改成你自己的
sudo cp /opt/tvbox/deploy/tvbox-build.service /etc/systemd/system/
sudo cp /opt/tvbox/deploy/tvbox-build.timer  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tvbox-build.timer

systemctl list-timers tvbox-build.timer    # 看定时器
sudo systemctl start tvbox-build.service    # 手动触发一次
journalctl -u tvbox-build -n 50             # 看日志
```

> 两种方式**二选一**，别同时开。管理后台也可注册成服务：复制 `deploy/tvbox-manager.service` 到 `/etc/systemd/system/` 后 `enable --now` 即可。

---

## 五、网页管理后台（可选）

不想每次都 ssh 传文件，可以起一个网页：上传 JSON、填远程 URL、粘贴内容，还能一键「重新构建」「构建并推送」。

```bash
cd /opt/tvbox
python3 manage.py
# 默认监听 0.0.0.0:8787, 浏览器打开 http://<内网IP>:8787
# 仅本机:  python3 manage.py --host 127.0.0.1
# 改端口:  python3 manage.py --port 9000
```

常用操作：

- **上传 / URL 抓取 / 粘贴内容** 添加新源
- **重新构建**（只构建）、**构建并推送**（构建后推送到仓库）
- **企业微信通知** 卡片：看绑定状态、发测试消息
- **失败黑名单**：查看被剔除的源，可「一键全部放出」立即重探

> ⚠ **该页面没有登录校验**，能访问端口就能操作，**只在局域网用，别暴露到公网**。
> 改完 `tvbox_core/` 代码要重启才生效：`systemctl restart tvbox-manager`。

---

## 六、部署后自检

```bash
bash /opt/tvbox/deploy/check.sh \
  https://cnb.cool/<账号>/<仓库名>/-/git/raw/main/www/tvbox.json
```

会检查：HTTP 200、禁缓存头、JSON 合法性、站点数量。

---

## 七、常见问题

| 现象 | 处理 |
|---|---|
| 电视加载源报网络错误 | 确认填的是 `/-/git/raw/` 地址，不是 `/-/blob/` |
| 改了源电视没变化 | 电视端手动清一下源缓存（脚本已带禁缓存头 + `updated` 时间戳） |
| 网页点「构建并推送」没推上去 | 看日志尾部：①项目没 `git init`；②`push.env` 令牌失效；③路径不对 |
| 推送报 401 | 令牌失效 → 去平台重发，更新 `deploy/push.env` 里的 `GIT_TOKEN` |
| 想确认通知绑没绑 | 网页「总览 → 企业微信通知」看状态，或点「发送测试消息」 |
| 某个站一直空白 | 多为 `csp_` 源，你的 App 不带该爬虫 → 换 lite 源或换 App 版本 |
| 黑名单源被跳过 | 达阈值的源默认等 7 天自动重探；想立刻重探 → 网页点「一键全部放出」 |
| 管理页改了没生效 | 页面在进程内存里，改完要 `systemctl restart tvbox-manager` |

---

## 八、常用命令速查

```bash
# 完整构建(拉远程源 + 探测存活 + 出产物)
python3 build.py --out www

# 只合并、不探测(离线调试)
python3 build.py --no-probe

# 改过核心代码后先跑离线回归测试(不联网, 秒级)
python3 tests/run_all.py

# 手动跑一次定时脚本(构建 + 推送)
/opt/tvbox/deploy/update.sh

# 通知相关
python3 build.py --notify-capture   # 绑定会话(首次)
python3 build.py --notify-test      # 发测试消息
python3 build.py --no-notify        # 本次关闭通知

# 调参
python3 build.py --workers 40 --timeout 6     # 网络好, 更快
python3 build.py --workers 12 --timeout 12    # 网络一般, 更稳
```

> 更多功能原理与实测数据见 **[README.md](README.md)**。
