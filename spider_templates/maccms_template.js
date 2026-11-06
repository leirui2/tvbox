/**
 * TVBox drpy 爬虫模板 —— 抓「苹果 CMS (MacCMS)」标准站
 *
 * 这是最推荐的第一个练手对象：MacCMS 结构全国统一，
 * 一个模板改 host 就能套用到绝大多数资源站。
 *
 * 用法：
 *   1. 把 host 改成你要抓的站
 *   2. 用浏览器打开该站，确认列表页/搜索页/详情页的 URL 格式
 *   3. 把这文件丢进 TVBox 的 js 目录（或用 spider 字段引用）
 */

var rule = {
    title: '示例采集站',
    host: 'https://example.com',

    // ========== 首页分类 ==========
    // 首页会显示这些分类，用户点了会跳到对应的分类列表
    class_name: '电影&电视剧&综艺&动漫',
    class_url: '1&2&3&4',

    // ========== 列表页 URL ==========
    // fypage 是「页码」占位符，TVBox 会自动替换
    // 常见格式：/vodshow/1-----------.html  （1 = 分类 id）
    url: '/vodshow/1-----------.html',

    // ========== 搜索 ==========
    // searchUrl 里 wd 是「搜索关键词」占位符
    // 常见格式：/vodsearch/-------------.html
    searchUrl: '/vodsearch/-------------.html',
    searchable: 1,     // 1 = 用 searchUrl 跳转搜索
    quickSearch: 1,    // 首页显示快速搜索框

    // ========== 分类筛选 ==========
    filterable: 1,     // 支持地区/年份/排序筛选
    filter: '{"cateId":"分类","area":"地区","year":"年份","order":"排序"}',
    filter_def: '{"cateId":[{"n":"电影","v":"1"},{"n":"电视剧","v":"2"}],' +
                '"area":[{"n":"全部","v":""},{"n":"大陆","v":"大陆"},{"n":"港台","v":"港台"}],' +
                '"year":[{"n":"全部","v":""},{"n":"2024","v":"2024"},{"n":"2023","v":"2023"}],' +
                '"order":[{"n":"最新","v":"time"},{"n":"最热","v":"hits"}]}',

    // ========== 详情页解析（关键，需要一点点 JS） ==========
    // MacCMS 站一般有 api.php/provide/vod 接口，优先走接口拿直链
    // lazy 里写 JS，从详情页 HTML 里提取「播放地址」列表
    lazy: `js:
        // input 是详情页地址，TVBox 会传入
        // 方法1：MacCMS 有标准 JSON 接口，直接请求拿 vod_play_url
        var vod_id = input.split('/').filter(Boolean).pop();
        var api = '${rule.host}' + '/api.php/provide/vod/?ac=detail&ids=' + vod_id;
        var html = request(api);
        var json = JSON.parse(html);
        var vod = json.list && json.list[0];
        if (!vod) return [];
        // vod_play_url 格式：第1集$链接#第2集$链接
        var urls = (vod.vod_play_url || '').split('#').filter(Boolean);
        var out = [];
        urls.forEach(function(u) {
            var parts = u.split('$');
            out.push({ name: parts[0], url: parts[1] });
        });
        return out;
    `
};
