'use strict'

/**
 * TVBox 自用 GitHub 反代 (Cloudflare Worker)
 *
 * 用法：
 *   https://你的域名/gh/https://raw.githubusercontent.com/xxx/yyy.json
 *   https://你的域名/gh/https://github.com/xxx/yyy/blob/main/yyy.json
 *
 * 改动说明（相对原版）：
 *   1. PREFIX 从 '/' 改为 '/gh/' —— 同域名下可同时挂自建后端（如 /api.php/provide/vod/）
 *   2. 增加简单访问控制：默认开启 UA 校验 + 可选白名单/限频，防止反代被公开滥用
 */

/**
 * 前端静态资源地址（访问根路径 / 时展示的界面）
 */
const ASSET_URL = 'https://ui.leirui.cc.cd/'

/**
 * 前缀：反代路径统一走 /gh/，与自建后端等其它服务隔离。
 * 注意：前后都带斜杠，少一个都会错。
 */
const PREFIX = '/gh/'

/**
 * 分支文件使用 jsDelivr 镜像的开关，0 关闭（默认）
 */
const Config = {
    jsdelivr: 0,
    // 是否校验 User-Agent（空 UA 直接拒绝，防止被 curl/脚本刷）
    requireUA: true,
    // 白名单：路径包含以下字符之一才放行。留空 = 只做域名白名单（GitHub 系）
    whiteList: [],
    // 简单限频：同一 IP 每分钟最大请求数（0 = 不限）
    rateLimitPerMin: 0,
}

/** @type {ResponseInit} */
const PREFLIGHT_INIT = {
    status: 204,
    headers: new Headers({
        'access-control-allow-origin': '*',
        'access-control-allow-methods': 'GET,POST,PUT,PATCH,TRACE,DELETE,HEAD,OPTIONS',
        'access-control-max-age': '1728000',
    }),
}

const exp1 = /^(?:https?:\/\/)?github\.com\/.+?\/.+?\/(?:releases|archive)\/.*$/i
const exp2 = /^(?:https?:\/\/)?github\.com\/.+?\/.+?\/(?:blob|raw)\/.*$/i
const exp3 = /^(?:https?:\/\/)?github\.com\/.+?\/.+?\/(?:info|git-).*$/i
const exp4 = /^(?:https?:\/\/)?raw\.(?:githubusercontent|github)\.com\/.+?\/.+?\/.+?\/.+$/i
const exp5 = /^(?:https?:\/\/)?gist\.(?:githubusercontent|github)\.com\/.+?\/.+?\/.+$/i
const exp6 = /^(?:https?:\/\/)?github\.com\/.+?\/.+?\/tags.*$/i
const exp7 = /^(?:https?:\/\/)?github\.com\/.+?(\/.+?)?$/i

function makeRes(body, status = 200, headers = {}) {
    headers['access-control-allow-origin'] = '*'
    return new Response(body, { status, headers })
}

function newUrl(urlStr) {
    try {
        return new URL(urlStr)
    } catch (err) {
        return null
    }
}

function checkUrl(u) {
    for (let i of [exp1, exp2, exp3, exp4, exp5, exp6, exp7]) {
        if (u.search(i) === 0) {
            return true
        }
    }
    return false
}

/**
 * 简单 IP 限频（内存态，Worker 冷启动/多实例下不精确，仅供防滥用）
 */
const rateMap = new Map()
function rateLimited(ip) {
    if (Config.rateLimitPerMin <= 0) return false
    const now = Date.now()
    const win = now - 60000
    const hits = (rateMap.get(ip) || []).filter(t => t > win)
    hits.push(now)
    rateMap.set(ip, hits)
    // 防止 Map 无限膨胀
    if (rateMap.size > 10000) {
        for (const k of rateMap.keys()) {
            if (rateMap.size <= 5000) break
            rateMap.delete(k)
        }
    }
    return hits.length > Config.rateLimitPerMin
}

async function fetchHandler(req) {
    const urlObj = new URL(req.url)

    // 简单限频
    const clientIp = req.headers.get('cf-connecting-ip') || 'unknown'
    if (rateLimited(clientIp)) {
        return makeRes('Too Many Requests', 429)
    }

    // UA 校验
    if (Config.requireUA && !req.headers.get('user-agent')) {
        return makeRes('Forbidden: missing User-Agent', 403)
    }

    // 只有根路径（首页）才加载前端界面
    if (urlObj.pathname === '/' || urlObj.pathname === '') {
        return fetch(ASSET_URL + urlObj.pathname)
    }

    // 非 /gh/ 前缀的路径：尝试拼 github.com 获取（保留原版行为）
    if (!urlObj.pathname.startsWith(PREFIX)) {
        const path = 'https://github.com' + urlObj.pathname + urlObj.search
        return httpHandler(req, path)
    }

    // 去掉前缀，拿到真实目标路径
    let path = urlObj.pathname.slice(PREFIX.length)
    // 支持 ?q= 参数跳转（原版兼容）
    const q = urlObj.searchParams.get('q')
    if (q) {
        return Response.redirect('https://' + urlObj.host + PREFIX + q, 301)
    }
    // 补全协议
    path = path.replace(/^https?:\/+/, 'https://')

    // 按 URL 类型分发
    if (path.search(exp1) === 0 || path.search(exp5) === 0 || path.search(exp6) === 0 || path.search(exp3) === 0 || path.search(exp7) === 0) {
        return httpHandler(req, path)
    } else if (path.search(exp2) === 0) {
        if (Config.jsdelivr) {
            const newUrl = path.replace('/blob/', '@').replace(/^(?:https?:\/\/)?github\.com/, 'https://cdn.jsdelivr.net/gh')
            return Response.redirect(newUrl, 302)
        } else {
            path = path.replace('/blob/', '/raw/')
            return httpHandler(req, path)
        }
    } else if (path.search(exp4) === 0) {
        if (Config.jsdelivr) {
            const newUrl = path.replace(/(?<=com\/.+?\/.+?)\/(.+?\/)/, '@$1').replace(/^(?:https?:\/\/)?raw\.(?:githubusercontent|github)\.com/, 'https://cdn.jsdelivr.net/gh')
            return Response.redirect(newUrl, 302)
        } else {
            return httpHandler(req, path)
        }
    } else {
        return makeRes('Unsupported URL', 403)
    }
}

function httpHandler(req, pathname) {
    const reqHdrRaw = req.headers

    // preflight
    if (req.method === 'OPTIONS' && reqHdrRaw.has('access-control-request-headers')) {
        return new Response(null, PREFLIGHT_INIT)
    }

    const reqHdrNew = new Headers(reqHdrRaw)

    let urlStr = pathname
    let flag = !Boolean(Config.whiteList.length)
    for (let i of Config.whiteList) {
        if (urlStr.includes(i)) {
            flag = true
            break
        }
    }
    if (!flag) {
        return new Response('blocked', { status: 403 })
    }
    if (urlStr.search(/^https?:\/\//) !== 0) {
        urlStr = 'https://' + urlStr
    }
    const urlObj = newUrl(urlStr)

    /** @type {RequestInit} */
    const reqInit = {
        method: req.method,
        headers: reqHdrNew,
        redirect: 'manual',
        body: req.body,
    }
    return proxy(urlObj, reqInit)
}

async function proxy(urlObj, reqInit) {
    const res = await fetch(urlObj.href, reqInit)
    const resHdrOld = res.headers
    const resHdrNew = new Headers(resHdrOld)
    const status = res.status

    if (resHdrNew.has('location')) {
        let _location = resHdrNew.get('location')
        if (checkUrl(_location)) {
            resHdrNew.set('location', PREFIX + _location)
        } else {
            reqInit.redirect = 'follow'
            return proxy(newUrl(_location), reqInit)
        }
    }

    // 允许跨域
    resHdrNew.set('access-control-expose-headers', '*')
    resHdrNew.set('access-control-allow-origin', '*')
    // 清理可能干扰的安全头
    resHdrNew.delete('content-security-policy')
    resHdrNew.delete('content-security-policy-report-only')
    resHdrNew.delete('clear-site-data')

    // 直接返回原始内容，不进行任何 HTML 重写
    return new Response(res.body, {
        status,
        headers: resHdrNew,
    })
}

export default {
    async fetch(request, env, ctx) {
        try {
            return await fetchHandler(request)
        } catch (err) {
            return makeRes('cfworker error:\n' + err.stack, 502)
        }
    }
}
