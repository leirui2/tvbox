# -*- coding: utf-8 -*-
"""构建流程编排(入口; 根目录 build.py 仅是本模块的薄入口)。

完整链路:
  加载本地源 -> (可选)远程拉取 -> 配置级去重 -> 合并(+rules 合并) -> 手动禁用清单
  -> 站点名推广文字清洗 -> 静态字段清洗 -> JAR 统计/兜底/可达性/死链救治/类名审计
  -> 站点 diff -> 存活探测(含 deep 内容验证) -> 安全阀(存活数低于阈值则拒绝覆盖)
  -> 产物输出 -> 企业微信通知(成功推结果 / 安全阀触发推异常 / 未捕获异常推崩溃)

用法:
  python3 build.py                  # 完整构建
  python3 build.py --no-probe       # 只合并去重(离线/调试)
  python3 build.py --no-fetch       # 不拉远程, 仅用 fetched/ 已有缓存
  python3 build.py --notify-capture # 绑定企业微信通知会话(首次使用)
  python3 build.py --notify-test    # 给已绑定会话发一条测试消息
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time
import traceback

from . import APP_DIR, OUT_DIR, emit_prog
from .fetch import fetch_remotes
from .jar import (apply_jar_fallback, audit_jar_classes, check_jar_urls,
                  log_jar_stats, rescue_dead_jars)
from .jsonio import load_json
from .merge import (clean_promo_names, config_fingerprint, dedup_configs,
                    is_source_config, merge_configs)
from .notify import (capture_chatid, notify_build_crashed,
                     notify_build_failed, notify_build_success, send_markdown,
                     set_enabled)
from .output import (atomic_write, site_diff, split_alive,
                     write_commented_json, write_report)
from .probe import (apply_disable_list, load_blacklist_file,
                    load_disable_list, pick_static_fields, run_probes,
                    save_blacklist_file)

def main():
    # Windows 控制台默认 GBK, 站点名/⚠ 等非 GBK 字符打印会崩;
    # 与 build.py 薄入口保持一致, 强制 stdout/stderr 用 UTF-8 + replace。
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    try:
        return _run()
    except Exception:
        # 未捕获异常 -> 先推崩溃告警, 再原样抛出(退出码非零,
        # cron/管理页/update.sh 自检都能照常感知失败)
        try:
            notify_build_crashed(traceback.format_exc())
        except Exception:
            pass
        raise


def _run():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-probe', action='store_true', help='不探测存活(离线合并)')
    ap.add_argument('--workers', type=int, default=128,
                    help='存活/静态探测并发数(默认 128)')
    ap.add_argument('--timeout', type=int, default=4,
                    help='单次探测超时秒数(默认 4)')
    ap.add_argument('--fetch-workers', type=int, default=128,
                    help='远程源拉取并发数(默认 128)')
    ap.add_argument('--fetch-timeout', type=int, default=4,
                    help='远程源拉取超时秒数(默认 4; urllib 为 socket 级超时, 不影响慢速持续下载)')
    ap.add_argument('--out', default='www')
    ap.add_argument('--src', default='sources', help='源 JSON 所在目录(默认 sources/)')
    ap.add_argument('--fetch-list', default='dc.txt',
                    help='远程源 URL 列表文件(默认 dc.txt), 为空则关闭远程拉取')
    ap.add_argument('--no-fetch', action='store_true',
                    help='不拉取远程源, 仅用 fetched/ 已有缓存')
    ap.add_argument('--fail-threshold', type=int, default=5,
                    help='同一源连续失败达到该次数即拉黑, 后续构建跳过(默认 5)')
    ap.add_argument('--warn-threshold', type=int, default=3,
                    help='健康分级: 探测成功但此前连续失败达到该次数(且未达拉黑阈值)的'
                         '站点, name 加 [⚠] 标记; 0 关闭(默认 3)')
    ap.add_argument('--no-blacklist', action='store_true',
                    help='禁用失败黑名单(不读不写, 每次都全量探测)')
    ap.add_argument('--blacklist-breaker', type=float, default=0.6,
                    help='熔断: 本轮连接级失败占比达到该值(且探测数>=--breaker-min-probes)'
                         '时判定网络异常, 放弃本轮黑名单写入; 0 关闭(默认 0.6)')
    ap.add_argument('--breaker-min-probes', type=int, default=30,
                    help='熔断触发的最小探测数, 防小样本误触(默认 30)')
    ap.add_argument('--blacklist-retry-days', type=int, default=7,
                    help='已达阈值的黑名单源超过该天数自动重新试探; 0 关闭(默认 7)')
    ap.add_argument('--min-sites', type=int, default=20,
                    help='存活站点下限, 低于此值判定为异常, 拒绝覆盖')
    ap.add_argument('--min-ratio', type=float, default=0.5,
                    help='相对上次存活数的最低比例, 低于则拒绝覆盖(防断网误清空)')
    ap.add_argument('--no-deep-check', action='store_true',
                    help='关闭 deep 内容验证(退回纯 HTTP 200 浅层探测)')
    ap.add_argument('--no-clean-names', action='store_true',
                    help='关闭站点名推广文字清洗(URL/联系方式/推广关键词/更新日期章)')
    ap.add_argument('--no-notify', action='store_true',
                    help='不推送企业微信通知(结果/告警都不推)')
    ap.add_argument('--notify-capture', action='store_true',
                    help='绑定企业微信通知会话: 运行后去企业微信给机器人发任意消息')
    ap.add_argument('--notify-test', action='store_true',
                    help='给已绑定会话发一条测试消息后退出')
    args = ap.parse_args()
    if args.no_notify:
        set_enabled(False)
    if args.notify_capture:
        ok, detail = capture_chatid()
        print('[notify-capture] %s' % detail)
        return 0 if ok else 1
    if args.notify_test:
        ok, detail = send_markdown(
            '**TVBox 通知测试** `%s`\n如果你看到这条消息, 说明绑定成功。'
            % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        print('[notify-test] %s' % detail)
        return 0 if ok else 1
    t0 = time.time()

    root = APP_DIR   # 仓库根目录(包所在目录的上一级)
    bl_path = os.path.join(root, 'probe_blacklist.json')
    if args.no_blacklist:
        blacklist, fetch_blacklist = {}, {}
    else:
        bl_data = load_blacklist_file(bl_path)
        blacklist = bl_data.get('entries') or {}
        fetch_blacklist = bl_data.get('fetch_entries') or {}
    src_dir = os.path.join(root, args.src) if not os.path.isabs(args.src) else args.src
    out_dir = os.path.join(root, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(out_dir, exist_ok=True)

    # ---- 远程源拉取: 抓取 dc.txt 里的远程源并缓存到 fetched/ ----
    fetched_dir = os.path.join(root, 'fetched')
    fetched_files = []
    do_fetch = (not args.no_fetch) and (not args.no_probe) and bool(args.fetch_list)
    if do_fetch:
        list_path = (args.fetch_list if os.path.isabs(args.fetch_list)
                     else os.path.join(root, args.fetch_list))
        fetched_files = fetch_remotes(list_path, fetched_dir,
                                      args.fetch_timeout, args.fetch_workers,
                                      blacklist=fetch_blacklist,
                                      threshold=args.fail_threshold)
    elif os.path.isdir(fetched_dir):
        fetched_files = sorted(glob.glob(os.path.join(fetched_dir, '*.json')))

    files = sorted(glob.glob(os.path.join(src_dir, '*.json')))
    files += fetched_files   # 远程缓存与本地源一并纳入
    configs = []
    skipped = []
    src_spiders = {}   # 源文件名 -> 其声明的顶层 spider(供 JAR 统计归票)
    src_fps = {}       # 源文件名 -> 内容指纹(本地源经 fetched 副本继承来源 URL)
    for fp in files:
        name = os.path.basename(fp)
        if name.startswith('_') or name in ('tvbox.json', 'tvbox.full.json',
                                             'report.json', 'url_map.json'):
            continue
        try:
            d = load_json(fp)
        except Exception as e:
            skipped.append((name, 'parse_error: %s' % e))
            print('[跳过] %s : %s' % (name, e), file=sys.stderr)
            continue
        if not is_source_config(d):
            skipped.append((name, 'not a source config (no sites/lives/parses)'))
            continue
        for cat in ('sites', 'lives', 'parses'):   # 记录来源文件, 供黑名单归属
            for it in d.get(cat, []) or []:
                if isinstance(it, dict):
                    it['_src'] = name
        d['_src'] = name   # 配置级来源标记(供去重日志), 不参与合并
        src_spiders[name] = str(d.get('spider', '') or '').strip()
        src_fps[name] = config_fingerprint(d)
        configs.append(d)
        print('[已加载] %s : sites=%d lives=%d parses=%d' % (
            name, len(d.get('sites', []) or []),
            len(d.get('lives', []) or []), len(d.get('parses', []) or [])))

    configs, dedup_stats = dedup_configs(configs, verbose=True)

    emit_prog('merge', 0, 1)
    print('\n合并 %d 个源文件...' % len(configs))
    merged = merge_configs(configs, verbose=True)
    emit_prog('merge', 1, 1)
    print('去重后 -> sites=%d lives=%d parses=%d flags=%d' % (
        len(merged.get('sites', [])), len(merged.get('lives', [])),
        len(merged.get('parses', [])), len(merged.get('flags', []))))

    # ---- 手动禁用清单: 从合并结果中移除被禁的源 ----
    dl_path = os.path.join(root, 'disable_list.json')
    disable_items = load_disable_list(dl_path)
    if disable_items:
        n_dis = apply_disable_list(merged, disable_items)
        if n_dis:
            print('手动禁用: 移除 %d 个条目 (清单 %d 条) -> sites=%d lives=%d parses=%d' % (
                n_dis, len(disable_items),
                len(merged.get('sites', [])), len(merged.get('lives', [])),
                len(merged.get('parses', []))))

    # ---- 站点名推广文字清洗(必须在站点 diff 之前: 清洗是确定性的,
    #      同一源名每次得到同一结果, diff 身份(api+name)才稳定) ----
    if not args.no_clean_names:
        clean_promo_names(merged, verbose=True)

    print('清洗静态资源字段 (wallpaper/logo/spider/danmaku)...')
    spider_cands = merged.get('_cand_spider') or []   # pick 会 pop 掉, 先留证
    pick_static_fields(merged, timeout=args.timeout,
                      do_probe=not args.no_probe, workers=args.workers)
    log_jar_stats(merged, src_spiders, spider_cands)

    # ---- JAR 投票制第 2 步: per-site jar 兜底(全局 spider 保持不变) ----
    jar_urls = {}   # 源文件名 -> 来源配置 URL(fetched 缓存) 或继承 URL(本地源)
    umap_path = os.path.join(fetched_dir, 'url_map.json')
    if os.path.exists(umap_path):
        try:
            loaded = json.load(open(umap_path, encoding='utf-8'))
            if isinstance(loaded, dict):
                jar_urls = dict(loaded)
        except Exception:
            jar_urls = {}
    # 本地源无来源 URL: 内容指纹与某个 fetched 副本相同 -> 继承其来源
    # (内容相同意味着 spider 相同, 相对路径解析结果一致)
    fp_to_url = {}
    for fpth in fetched_files:
        u = jar_urls.get(os.path.basename(fpth))
        if not u:
            continue
        try:
            fpr = config_fingerprint(load_json(fpth))
        except Exception:
            continue
        if fpr and fpr not in fp_to_url:
            fp_to_url[fpr] = u
    inherited = 0
    for name, fpr in src_fps.items():
        if name not in jar_urls and fpr and fpr in fp_to_url:
            jar_urls[name] = fp_to_url[fpr]
            inherited += 1
    apply_jar_fallback(merged, src_spiders, jar_urls=jar_urls)
    if inherited:
        print('[JAR] %d 个本地源经内容相同的 fetched 副本继承了来源 URL' % inherited)

    # ---- 站点增删 diff(与上次构建的快照对比) ----
    diff_added, diff_removed = site_diff(
        merged, os.path.join(root, '.site_snapshot.json'))

    # ---- JAR 可达性探测: 兜底写入的 jar 地址是否是死链 ----
    if not args.no_probe:
        _ja, jd = check_jar_urls(merged, timeout=6, workers=16)
        rescue_dead_jars(merged, jd, os.path.join(fetched_dir, 'jar_cache'))
        audit_jar_classes(merged, os.path.join(fetched_dir, 'jar_cache'),
                          alive_urls=_ja)

    if not args.no_probe:
        print('探测存活中 (workers=%d timeout=%ds)...' % (args.workers, args.timeout))
        probe_stats = run_probes(merged, args.timeout, args.workers,
                                 blacklist=blacklist,
                                 threshold=args.fail_threshold,
                                 warn_threshold=args.warn_threshold,
                                 deep_check=not args.no_deep_check,
                                 breaker_ratio=args.blacklist_breaker,
                                 breaker_min_probes=args.breaker_min_probes,
                                 retry_days=args.blacklist_retry_days)
        if not args.no_blacklist:
            save_blacklist_file(bl_path, blacklist, fetch_blacklist,
                                args.fail_threshold,
                                retry_days=args.blacklist_retry_days)
            n_blocked = sum(1 for e in blacklist.values()
                            if int(e.get('fail_count', 0)) >= args.fail_threshold)
            n_fblocked = sum(1 for e in fetch_blacklist.values()
                             if int(e.get('fail_count', 0)) >= args.fail_threshold)
            print('失败黑名单: 源存活 %d 个累计(%d 已达阈值), 远程拉取 %d 个累计(%d 已达阈值), 已写入 %s'
                  % (len(blacklist), n_blocked, len(fetch_blacklist), n_fblocked,
                     os.path.basename(bl_path)))
        if probe_stats.get('tripped'):
            ok, detail = notify_build_failed(
                '黑名单熔断: 本轮 %d/%d 连接失败, 判定网络异常'
                % (probe_stats['conn_error'], probe_stats['probed']),
                detail='已放弃本轮黑名单写入(恢复探测前计数); '
                       '请检查服务器网络/DNS。产物仍按本轮探测输出, '
                       '存活数异常时安全阀会拒绝覆盖。')
            print('[notify] %s' % detail, file=sys.stderr)
        elif probe_stats.get('retried'):
            print('黑名单重试探: %d 个已达阈值源重新探测, %d 个恢复'
                  % (probe_stats['retried'], probe_stats['retry_recovered']))
    else:
        print('跳过存活探测 (--no-probe)')

    # ---- 远程源健康: dc.txt 里连续拉取失败已达阈值的条目(建议从清单移除) ----
    fetch_dead = []
    for u, e in (fetch_blacklist or {}).items():
        try:
            fc = int(e.get('fail_count', 0) or 0)
        except (TypeError, ValueError):
            fc = 0
        if fc >= args.fail_threshold:
            fetch_dead.append({'url': u, 'name': e.get('name') or u,
                               'fail_count': fc,
                               'last_fail': e.get('last_fail') or ''})
    fetch_dead.sort(key=lambda x: -x['fail_count'])
    if fetch_dead:
        print('远程源健康: %d 个 dc.txt 条目已达拉取黑名单阈值, '
              '详见报告"建议移除"清单' % len(fetch_dead))

    alive, lite, full = split_alive(merged)
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    alive['updated'] = ts
    lite['updated'] = ts
    full['updated'] = ts

    # ---- 安全阀: 防止服务器断网/大范围异常时把好源覆盖成空的 ----
    if not args.no_probe:
        new_alive = len(alive.get('sites', []))
        prev_alive = 0
        prev_path = os.path.join(out_dir, 'tvbox.json')
        if os.path.exists(prev_path):
            try:
                with open(prev_path, encoding='utf-8') as f:
                    prev_alive = len(json.load(f).get('sites', []))
            except Exception:
                prev_alive = 0
        floor = max(args.min_sites, int(prev_alive * args.min_ratio))
        if new_alive < floor:
            print('\n[安全阀] 存活站点 %d < 阈值 %d (上次 %d), 判定为异常, 已跳过覆盖。'
                  % (new_alive, floor, prev_alive), file=sys.stderr)
            print('[安全阀] 请检查服务器网络/ DNS 后重跑, 或用 --min-sites / --min-ratio 调整。',
                  file=sys.stderr)
            print('构建总耗时: %.1f 秒' % (time.time() - t0), file=sys.stderr)
            ok, detail = notify_build_failed(
                '安全阀触发: 存活站点 %d < 阈值 %d (上次 %d)'
                % (new_alive, floor, prev_alive),
                detail='请检查服务器网络/ DNS 后重跑, '
                       '或用 --min-sites / --min-ratio 调整')
            print('[notify] %s' % detail, file=sys.stderr)
            return 1
        print('\n[安全阀] 通过: 存活 %d (上次 %d, 阈值 %d)' % (new_alive, prev_alive, floor))

    emit_prog('output', 0, 1)
    atomic_write(os.path.join(out_dir, 'tvbox.json'),
                 json.dumps(alive, ensure_ascii=False, indent=2))
    atomic_write(os.path.join(out_dir, 'tvbox.lite.json'),
                 json.dumps(lite, ensure_ascii=False, indent=2))
    atomic_write(os.path.join(out_dir, 'tvbox.full.json'),
                 json.dumps(full, ensure_ascii=False, indent=2))
    write_commented_json(merged, os.path.join(out_dir, 'tvbox.commented.json'), ts)
    write_report(merged, os.path.join(out_dir, 'report.json'),
                 os.path.join(out_dir, 'report.md'), out_dir,
                 fetch_dead=fetch_dead)
    emit_prog('output', 1, 1)

    # ---- 企业微信通知: 构建结果(失败/崩溃的推送在各自路径上) ----
    try:
        with open(os.path.join(out_dir, 'report.json'), encoding='utf-8') as f:
            summ = json.load(f).get('summary') or {}
    except Exception:
        summ = {}
    ok, detail = notify_build_success(summ, diff_added, diff_removed,
                                      time.time() - t0,
                                      counts={'sites': len(alive.get('sites', [])),
                                              'lives': len(alive.get('lives', [])),
                                              'parses': len(alive.get('parses', []))})
    print('[notify] %s' % detail)

    print('\n输出:')
    print('  %s/tvbox.json      (主源: 存活+客户端依赖, 失效已剔除)' % args.out)
    print('  %s/tvbox.lite.json (稳妥源: 仅实测存活, 无爬虫依赖)' % args.out)
    print('  %s/tvbox.full.json (全量审计: 含失效标记)' % args.out)
    print('  %s/tvbox.commented.json (失效源以 /* */ 注释保留, 人工查看用)' % args.out)
    print('  %s/report.md       (失效清单)' % args.out)
    if skipped:
        print('\n未处理的文件:')
        for n, r in skipped:
            print('  %s : %s' % (n, r))

    print('\n构建总耗时: %.1f 秒' % (time.time() - t0))


if __name__ == '__main__':
    main()
